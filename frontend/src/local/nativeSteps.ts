import {t} from '@/i18n';
/** Claude Code's own tool calls in full access, and web searches from either provider in any mode, in the words Progress
 * uses. ``path`` is a project-relative file (or "screenshot <name>"), ``command`` the command's own description, a search
 * pattern, a web host or what was searched on the web. Never tool output. */
export const NATIVE:Record<string,[string,string]>={
 'native:Bash':[t('Running'),t('Ran')],'native:Read':[t('Reading'),t('Read')],'native:Edit':[t('Editing'),t('Edited')],'native:Write':[t('Writing'),t('Wrote')],
 'native:Glob':[t('Finding files'),t('Found files')],'native:Grep':[t('Searching for'),t('Searched for')],'native:WebFetch':[t('Fetching'),t('Fetched')],
 'native:WebSearch':[t('Searching the web'),t('Searched the web')],
};
export function nativeStep(token:{tool:string;path?:string|null;command?:string|null;prose?:boolean;connector?:string|null}):{present:string;past:string;subject?:string;file:boolean;prose?:boolean}|undefined{
 // One of your MCP servers or claude.ai connectors: its name, and the tool as the subject. Never its arguments.
 if(token.tool==='native:Connector'){
  const name=token.connector||t('a connector');
  return {present:t('Using {name}',{name}),past:t('Used {name}',{name}),subject:token.command??undefined,file:false};
 }
 const verbs=NATIVE[token.tool];
 if(!verbs)return undefined;
 const shot=typeof token.path==='string'&&token.path.startsWith('screenshot ');
 if(shot)return {present:t('Looking at a screenshot'),past:t('Looked at a screenshot'),subject:token.path!.slice(11),file:true};
 if(token.tool==='native:Read'||token.tool==='native:Edit'||token.tool==='native:Write')return {present:verbs[0],past:verbs[1],subject:token.path??undefined,file:true};
 // A search reads "Searched the web for <query>". Codex names the query only when the search ends.
 if(token.tool==='native:WebSearch'&&token.command)return {present:t('{verb} for',{verb:verbs[0]}),past:t('{verb} for',{verb:verbs[1]}),subject:token.command,file:false};
 // A command Claude Code described in words reads as the step itself ("Run the tests"), not as code after "Ran".
 if(token.tool==='native:Bash'&&token.prose&&token.command)return {present:token.command,past:token.command,file:false,prose:true};
 return {present:verbs[0],past:verbs[1],subject:token.command??token.path??undefined,file:false};
}
