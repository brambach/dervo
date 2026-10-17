import {t} from '@/i18n';
/** Timeline author label for provider rows, derived from the bound provider. Never a guess. */
export function providerAuthor(provider?:string|null):string {
  if(provider==='codex')return 'Codex';
  if(provider==='claude')return 'Claude Code';
  return provider?t('Provider ({provider})',{provider}):t('Provider (unknown)');
}

/** The Claude names the timeline shows. models.ts's lists and this must agree; modelCheck.ts says when they don't. */
export const CLAUDE_NAMES:Record<string,string>={'claude-fable-5-1':'Fable 5.1','claude-opus-5-5':'Opus 5.5','claude-opus-5':'Opus 5','claude-sonnet-5-5':'Sonnet 5.5','claude-sonnet-5':'Sonnet 5','claude-fable-5':'Fable 5','claude-haiku-4-5':'Haiku 4.5','claude-haiku-4-5-20251001':'Haiku 4.5'};
/** A model's display name: the published Claude names, a catalog name when one is given, otherwise the id tidied
 * ("gpt-5.5-codex" reads "GPT-5.5 Codex"). The raw id stays available as a tooltip where it's shown. */
export function modelName(model?:string|null,catalogName?:string|null):string {
  if(!model)return '';
  if(catalogName)return catalogName;
  if(CLAUDE_NAMES[model])return CLAUDE_NAMES[model];
  return model.split('-').map(part=>/^gpt$/i.test(part)?'GPT':/^o\d/.test(part)?part:part.charAt(0).toUpperCase()+part.slice(1)).join(' ').replace(/^GPT (\d)/,'GPT-$1');
}
