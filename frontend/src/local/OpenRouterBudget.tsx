import {useState, type FormEvent} from 'react';
import {t} from '@/i18n';

export type BudgetStatus = {configured: boolean; revision: number; totalUsd: string|null; perRequestUsd: string|null;
  usedUsd: string; reservedUsd: string; remainingUsd: string|null; uncertainRequests: number; costExceededReservation: boolean; scope: string};

export function OpenRouterBudget({budget, disabled, onSave}: {budget: BudgetStatus; disabled: boolean;
  onSave: (values: {totalUsd: string; perRequestUsd: string; revision: number}) => void}) {
  const [total, setTotal] = useState(budget.totalUsd ?? '');
  const [perRequest, setPerRequest] = useState(budget.perRequestUsd ?? '');
  function save(event: FormEvent) {
    event.preventDefault();
    onSave({totalUsd: total, perRequestUsd: perRequest, revision: budget.revision});
  }
  return <section aria-label={t('OpenRouter spending limits')} className="dv-or-part">
    <div><h4 className="dv-set-name">{t('Spending limits')}</h4><p className="dv-set-line">{t('Choose a limit for each request and a total budget for this workspace. The total doesn’t reset each day.')}</p></div>
    {budget.configured && <dl className="dv-or-facts">
      <div><dt>{t('Used')}</dt><dd>${budget.usedUsd}</dd></div>
      <div><dt>{t('Reserved')}</dt><dd>${budget.reservedUsd}</dd></div>
      <div><dt>{t('Available')}</dt><dd>${budget.remainingUsd}</dd></div>
    </dl>}
    {(budget.uncertainRequests > 0 || budget.costExceededReservation) && <p role="status" className="dv-card-text">
      {budget.costExceededReservation ? t('A charge exceeded its reservation. Usage needs review before another request can start.') : t('{count} requests have an unconfirmed charge. That allowance stays reserved until usage is checked.', {count: budget.uncertainRequests})}
    </p>}
    <form onSubmit={save} className="dv-or-part">
      <div className="dv-or-grid">
        <label className="dv-field"><span>{t('Per request (USD)')}</span><input className="dv-input" type="text" inputMode="decimal" required maxLength={8} value={perRequest} disabled={disabled} placeholder="e.g. 0.50" onChange={event => setPerRequest(event.target.value)}/></label>
        <label className="dv-field"><span>{t('Workspace budget (USD)')}</span><input className="dv-input" type="text" inputMode="decimal" required maxLength={8} value={total} disabled={disabled} placeholder="e.g. 5.00" onChange={event => setTotal(event.target.value)}/></label>
      </div>
      <button type="submit" className="dv-btn dv-btn-ghost dv-btn-sm" style={{alignSelf: 'flex-start'}} disabled={disabled}>{t('Save limits')}</button>
    </form>
    <p className="dv-field-note">{budget.scope} {t('Saving these limits doesn’t enable paid requests. Agent execution still needs to be connected.')}</p>
  </section>;
}
