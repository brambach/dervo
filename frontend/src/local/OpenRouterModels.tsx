import {useEffect, useState, type FormEvent} from 'react';
import {formatNumber, t} from '@/i18n';

type Endpoint = {provider: string; coveredProviders: string[]; contextTokens: number; maxOutputTokens: number|null;
  pricing: Record<string,string>|null; selectable: boolean; reason: string};
export type CheckedRoutes = {modelId: string; endpoints: Endpoint[]};
export type Routing = {revision: number; selection: {modelId: string; provider: string; maxOutputTokens: number; allowFallbacks: false}|null};
type Selection = {modelId: string; provider: string; maxOutputTokens: number; revision: number};

function Providers({checked, routing, disabled, onSave}: {checked: CheckedRoutes; routing: Routing; disabled: boolean; onSave: (selection: Selection) => void}) {
  const previous = routing.selection?.modelId === checked.modelId ? routing.selection : null;
  const [provider, setProvider] = useState(previous?.provider ?? '');
  const [tokens, setTokens] = useState(previous ? String(previous.maxOutputTokens) : '');
  const endpoint = checked.endpoints.find(row => row.provider === provider && row.selectable);
  const maximum = endpoint ? Math.min(endpoint.maxOutputTokens ?? endpoint.contextTokens, 100000) : 100000;
  function save(event: FormEvent) {
    event.preventDefault();
    if (!endpoint) return;
    onSave({modelId: checked.modelId, provider, maxOutputTokens: Number(tokens), revision: routing.revision});
  }
  return <form onSubmit={save} className="dv-or-part">
    <p className="dv-set-name">{t('Providers for {model}', {model: checked.modelId})}</p>
    <div role="group" aria-label={t('Choose a provider')} className="dv-or-grid">
      {checked.endpoints.map(row => <button key={row.provider} type="button" disabled={disabled || !row.selectable}
        aria-pressed={provider === row.provider} onClick={() => setProvider(row.provider)}
        className="dv-or-choice">
        <span className="dv-or-name">{row.provider}</span>
        {row.coveredProviders.length > 1 && <span className="dv-or-small">{t('Includes {count} provider variants. Prices below use the highest checked rate.', {count: row.coveredProviders.length})}</span>}
        <span className="dv-or-small">{row.selectable ? t('{tokens} token context', {tokens: formatNumber(row.contextTokens)}) : row.reason}</span>
        {row.pricing && <span className="dv-or-small">{t('{input} input / {output} output per 1M tokens', {input: `$${Number(row.pricing.prompt) * 1000000}`, output: `$${Number(row.pricing.completion) * 1000000}`})}</span>}
      </button>)}
    </div>
    {!checked.endpoints.length && <p className="dv-card-text">{t('No providers were returned for this model. Choose another model or check again.')}</p>}
    <label className="dv-field"><span>{t('Maximum reply tokens')}</span>
      <input className="dv-input" type="number" min={1} max={maximum} step={1} required value={tokens} disabled={disabled || !endpoint}
        placeholder="e.g. 2048" onChange={event => setTokens(event.target.value)}/></label>
    <p className="dv-field-note">{t('Dervo uses this model and provider only. If the route can’t run, it stops instead of choosing a replacement. Displayed prices use the highest checked context-tier rates, before discounts. They don’t include every possible charge.')}</p>
    <button type="submit" className="dv-btn dv-btn-ghost dv-btn-sm" style={{alignSelf: 'flex-start'}} disabled={disabled || !endpoint || !tokens}>{t('Save model and provider')}</button>
  </form>;
}

export function OpenRouterModels({models, checked, routing, disabled, onCheck, onSave}: {
  models: {id: string}[]; checked: CheckedRoutes|null; routing: Routing; disabled: boolean;
  onCheck: (model: string) => void; onSave: (selection: Selection) => void;
}) {
  const [query, setQuery] = useState('');
  // A saved route is one row with Change route (connected-accounts-openrouter.html); the search opens in its place.
  const [changing, setChanging] = useState(!routing.selection);
  useEffect(() => {if (routing.selection) setChanging(false);}, [routing.revision]);  // a saved change folds back to its row
  const matches = models.filter(row => row.id.toLowerCase().includes(query.toLowerCase().trim()));
  if (routing.selection && !changing) return <section aria-label={t('OpenRouter model and provider')} className="dv-or-part">
    <div className="dv-set" aria-label={t('Saved OpenRouter route')} style={{padding: 0}}>
      <div><p className="dv-set-name">{t('{model} through {provider}', {model: routing.selection.modelId, provider: routing.selection.provider})}</p>
        <p className="dv-set-line">{t('Up to {tokens} reply tokens. Checked again before every request, and never swapped for another route.', {tokens: formatNumber(routing.selection.maxOutputTokens)})}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={disabled} onClick={() => setChanging(true)}>{t('Change route')}</button></div>
    </div>
  </section>;
  return <section aria-label={t('OpenRouter model and provider')} className="dv-or-part">
    <div><h4 className="dv-set-name">{t('Model and provider')}</h4>
      <p className="dv-set-line">{t('Find a model, check its providers, then save the route you want to use.')}</p></div>
    {routing.selection && <div aria-label={t('Saved OpenRouter route')}>
      <p className="dv-set-name">{t('Saved: {model}', {model: routing.selection.modelId})}</p>
      <p className="dv-set-line">{routing.selection.provider} · {t('up to {tokens} reply tokens', {tokens: formatNumber(routing.selection.maxOutputTokens)})}</p>
      <p className="dv-field-note">{t('A saved route still needs fresh checks before sending.')}</p>
    </div>}
    <input className="dv-input" type="search" aria-label={t('Find an OpenRouter model')} placeholder={t('Find a model…')} value={query} onChange={event => setQuery(event.target.value)}/>
    <div role="group" aria-label={t('Available OpenRouter models')} className="dv-or-list">
      {matches.slice(0,12).map(row => <button key={row.id} type="button" disabled={disabled} onClick={() => onCheck(row.id)}
        className="dv-or-model">
        <span className="dv-or-name">{row.id}</span><span className="dv-or-small">{t('Check providers')}</span>
      </button>)}
      {!matches.length && <p className="dv-card-text">{t('No matching models in your checked catalog.')}</p>}
    </div>
    {matches.length > 12 && <p className="dv-field-note">{t('Showing 12 of {count}. Search to narrow the list.', {count: matches.length})}</p>}
    {checked && <Providers key={`${checked.modelId}:${routing.revision}`} checked={checked} routing={routing} disabled={disabled} onSave={onSave}/>}
  </section>;
}
