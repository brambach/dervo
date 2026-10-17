import {useState} from 'react';
import {local} from './api';
import {StatusGlyph} from './StatusGlyph';
import {HEAVY_EFFORTS,claudeLine,claudeModelsFor,codexLine,defaultEffort,effortDescription,effortsFor,modelLabel,retirementFor,thinkingLine,type Account,type CatalogModel} from './models';
import {RetireNote,ThinkingLevels,useCatalog} from './ModelPicker';
import {t} from '@/i18n';

/** What Home's own replies run on in this project, as the host's listing says it (Orchestrator.availability). */
export type HomeModel={provider?:'claude'|'codex'|null;profile_id?:string|null;model?:string|null;model_id?:string|null;chosen?:boolean;effort?:string|null};

/** Dervo's default for Home on each provider, in words: the one row that saves no model. */
export const HOME_DEFAULT_LINE:Record<'claude'|'codex',string>={
  claude:t('Sonnet. Quick, and light on your limits.'),
  codex:t('GPT-6-Sol. Reads the project well; replies take a little longer.')};

/** The rows: Dervo's default first, then the account's models. Claude's from Dervo's list for this Claude Code, Codex's
 * from the account's catalog, hidden ones left out. */
export function homeModelRows(provider:'claude'|'codex',claudeVersion:string|null,catalog?:CatalogModel[]|null,plan?:string|null):{id:string|null;label:string;line:string}[]{
  const models=provider==='claude'
    ?claudeModelsFor(claudeVersion).map(m=>({id:m.id as string|null,label:m.label,line:claudeLine(m.id,plan)}))
    :(catalog??[]).filter(m=>!m.hidden).map(m=>({id:m.model as string|null,label:m.displayName??modelLabel('codex',m.model,catalog??undefined),line:codexLine(m)}));
  return [{id:null,label:t('Dervo’s default'),line:HOME_DEFAULT_LINE[provider]},...models];
}

/** Home's model and thinking for this project (Bryce, 2 Oct: the person's pick, as it is for threads). Opened from the
 * foot of Home's agent picker; a pick saves at once through orchestrator_home_model and the listing comes back. */
export function HomeModelSetting({home,account,claudeVersion,projectId,profileId,onSaved,onBack}:{home:HomeModel;account?:Account;claudeVersion:string|null;
  projectId:string;profileId:string;onSaved:(listing:unknown)=>void;onBack:()=>void}){
  const provider=home.provider==='codex'?'codex':'claude';
  const catalog=useCatalog(provider==='codex'?account?.profile:undefined,true);
  const models=catalog.data?.state==='catalog'?catalog.data.models:undefined;
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[more,setMore]=useState(false);
  const chosen=home.chosen?home.model_id??null:null;
  // Thinking is for the model that would run: the pick, or the default's own ID (Claude's default is an alias).
  const running=home.model_id??'claude-sonnet-5-5';
  const levels=effortsFor(provider,running,models);
  const heavy=levels.filter(l=>HEAVY_EFFORTS.includes(l)),light=levels.filter(l=>!HEAVY_EFFORTS.includes(l));
  const unfolded=more||(!!home.effort&&heavy.includes(home.effort));
  const rows=homeModelRows(provider,claudeVersion,models,account?.profile.plan);
  async function save(model:string|null,effort:string|null,label:string|null){
    setBusy(true);setError('');
    try{onSaved(await local('orchestrator_home_model',{projectId,profileId,provider,model,effort,...(model&&label?{label}:{})}));}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy(false);}
  }
  const label=(id:string|null)=>rows.find(r=>r.id===id)?.label??null;
  // Home saved on a model that's leaving Codex: say so and move it with one click, never by itself.
  const leaving=provider==='codex'&&chosen?retirementFor(chosen,models):null;
  return <div className="dv-pk-models dv-home-model">
    <div className="dv-pk-head"><span className="dv-title">{t('Home’s own replies')}</span><span className="dv-caption">{t('This project')}</span></div>
    {leaving&&models?<RetireNote retirement={leaving} catalog={models} busy={busy}
      stays={t('Home’s replies here stay on it until you move them.')} gone={t('Home can’t answer here until you move its replies.')}
      move={model=>t('Move Home to {model}',{model})}
      onMove={(id,name)=>void save(id,home.effort&&effortsFor(provider,id,models).includes(home.effort)?home.effort:null,name)}/>
    :<p className="dv-pk-note" role="note">{account?t('Home reads your records and answers here on {account}. A lighter model answers sooner and uses less of your plan.',{account:account.name}):t('Home reads your records and answers here. A lighter model answers sooner and uses less of your plan.')}</p>}
    <div className="dv-home-launch-scroll">
      {provider==='codex'&&!models?<p className="dv-pk-note" role="status">{catalog.error?t('Couldn’t load the models: {error}',{error:catalog.error}):t('Loading the models…')}</p>
        :<div className="dv-pk-list" role="listbox" aria-label={t('Home’s model')}>
          {rows.map(row=>{const current=row.id===chosen;
            // Picking another model keeps the level only when that model offers it.
            const keep=home.effort&&row.id&&effortsFor(provider,row.id,models).includes(home.effort)?home.effort:null;
            return <button key={row.id??'default'} type="button" role="option" className="dv-pk-model" aria-selected={current} disabled={busy}
              onClick={()=>{if(!current)void save(row.id,row.id?keep:null,row.label);}}>
              <b>{row.label}</b><small>{row.line}</small>{current&&<svg className="dv-pk-tick" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>}
            </button>;})}
        </div>}
      {error&&<p className="dv-pk-error" role="alert"><StatusGlyph state="failed"/>{error}</p>}
      {levels.length>0&&<div className="dv-pk-sec" role="group" aria-label={t('Thinking for Home')}>
        <div className="dv-pk-row-head"><span className="dv-label">{t('Thinking')}</span><span className="dv-caption">{t('for Home’s replies')}</span></div>
        <ThinkingLevels value={home.effort??null} light={light} heavy={unfolded?heavy:[]} more={heavy.length>0&&!unfolded} disabled={busy}
          describe={level=>effortDescription(running,level,models)}
          onPick={level=>void save(chosen,level,label(chosen))} onMore={()=>setMore(true)}/>
        {/* Claude's default is the CLI's Sonnet alias, so its line names no model version. */}
        <p className="dv-pk-line">{thinkingLine(home.effort,defaultEffort(provider,running,models),home.model_id??undefined,provider)}</p>
      </div>}
    </div>
    <div className="dv-pk-foot">
      <span>{t('Home answers with {model}. Threads keep their own model.',{model:home.model??t('its default')})}</span>
      <span className="dv-home-launch-actions"><button type="button" className="dv-btn dv-btn-quiet" onClick={onBack}>{t('Back')}</button></span>
    </div>
  </div>;
}
