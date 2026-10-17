import {t} from '@/i18n';
// Published exact IDs. Account access is checked by Claude when a task starts.
// https://code.claude.com/docs/en/model-config (checked 15 September 2026).
const choices=[['claude-sonnet-5','Sonnet 5'],['claude-opus-5','Opus 5'],['claude-fable-5','Fable 5'],['claude-fable-5-1','Fable 5.1']];
export function ClaudeModels({model,onSelect,disabled=false}:{model:string;onSelect:(id:string)=>void;disabled?:boolean}){
  return <><div className="dervo-setup-models" role="group" aria-label={t('Claude models')}>{choices.map(([id,label])=><button type="button" key={id} disabled={disabled} aria-pressed={model===id} onClick={()=>onSelect(id)}><strong>{label}</strong><span>{id}</span></button>)}</div><p className="dervo-setup-footnote">{t('These are published models. Claude checks access when a task starts. Fable may use paid usage credits under your plan.')}</p></>;
}
