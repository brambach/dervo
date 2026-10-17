import type {SettingsPage} from './DervoSettings';
import {language,t} from '@/i18n';

export type SettingsDestination=SettingsPage|'feedback';

// Only index controls that the destination actually offers. Access to a destination is
// checked by SettingsNav before matching, so search can't reveal an admin-only page.
// The words are matched in English and in the window's language (each list has its translation in the catalog), so a
// Portuguese window still finds "theme". Preferences also takes Portuguese words for its Language row, so you can find
// the language setting from English too.
// i18n-keys: search words, translated as whole lists through t() in termsFor
const TERMS={
  model:'account model models thinking reasoning effort new threads default provider routing automatically automatic stop switching accounts running low limits fallback',
  access:'access permissions read edit write files folders commands full access sandbox computer browser connectors tools',
  checks:'checks tests review reviewer approval approve acceptance finished changes timeout',
  helpers:'helper agents subagents delegation collaborate collaboration parallel workers',
  agents:'agents mention routines schedule scheduled jobs cron heartbeat assistant reviewer verifier gardener brief skills',
  cloud:'send to cloud remote background claude codex environment branch teleport',
  general:'general project name rename folder path finder archive delete remove',
  account:'account dervo sign in login email beta access sign out logout',
  preferences:'preferences appearance language languages idioma lingua portugues english ingles theme light dark system notifications alerts sounds window glass transparency usage sharing analytics privacy reduce reduced motion animation shimmer accessibility sleep awake caffeinate battery lid phone',
  accounts:'connected accounts provider login sign in sign out authenticate authentication claude codex chatgpt openrouter api key subscription usage limits balance',
  updates:'updates version release releases beta download install licences licenses',
  testers:'testers beta invite invitations waitlist approve approval access people usage errors',
  feedback:'send feedback bug report idea confusing help problem',
} as const satisfies Record<SettingsDestination,string>;
const FILLER=new Set(['a','an','and','can','change','do','for','how','i','in','is','me','my','of','on','please','setting','settings','the','to','turn','want','where',
  'o','os','as','de','da','das','dos','e','em','no','na','um','uma','eu','meu','minha','para','como','onde','quero','mudar','trocar','alterar','configuracao','configuracoes']);
/** Lower case without accents, so "língua" finds "lingua" and "configurações" is filler. */
const fold=(text:string)=>text.normalize('NFD').replace(/\p{M}/gu,'').toLocaleLowerCase();

function termsFor(destination:SettingsDestination):string{
  const english=TERMS[destination];
  return fold(language()==='en'?english:`${english} ${t(english)}`);
}

export function matchesSettingsSearch(destination:SettingsDestination,query:string):boolean{
  const words=fold(query).match(/[\p{L}\p{N}]+/gu)??[];
  const useful=words.filter(word=>!FILLER.has(word));
  const terms=termsFor(destination);
  return useful.every(word=>terms.includes(word));
}

export function settingsScope(page:SettingsDestination,project:string):string{
  if(page==='model')return t('New threads in {project}',{project});
  if(['access','checks','helpers','cloud','general'].includes(page))return t('Project: {project}',{project});
  // The nav group's word: "Your Dervo account" above Account and "Your Dervo sign-in" said it three times (polish, 8 Oct).
  if(page==='account')return t('You');
  if(page==='testers')return t('Dervo admin');
  if(page==='feedback')return t('Send a report');
  return t('This Mac');
}

export function settingsSearchHelp(query:string,hasProject:boolean):string{
  if(/\b(memory|knowledge|rules|skills)\b/i.test(query))return hasProject
    ?t('Project memory, rules and skills are in Knowledge. Go back to the project and open Knowledge in the sidebar.')
    :t('Project memory, rules and skills are in Knowledge. Choose a project first, then open Knowledge in its sidebar.');
  return hasProject?t('Try model, notifications or sign in.'):t('Choose a project to search its settings, or try notifications or sign in.');
}
