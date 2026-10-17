import {useEffect, useRef, useState, type FormEvent} from 'react';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local} from './api';
import {SettingsSection} from './DervoControls';
import {Meter, OpenCard} from './SettingsKit';
import {OpenRouterBudget, type BudgetStatus} from './OpenRouterBudget';
import {OpenRouterModels, type CheckedRoutes, type Routing} from './OpenRouterModels';
import {t} from '@/i18n';

type Status = {available: boolean; context?: string; state?: string; detail?: string; canCheck?: boolean; busy?: boolean;
  budget?: BudgetStatus|null;
  routing?: Routing|null; checkedRoutes?: CheckedRoutes|null;
  connection?: {limits: {limitUsd: number|null; remainingUsd: number|null; usedTodayUsd: number}; models: {id: string}[]}|null};
const usd = (value: number) => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 4}).format(value);

export function OpenRouterSettings() {
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [sending, setSending] = useState(false);
  const [replacing, setReplacing] = useState(false), [routeOpen, setRouteOpen] = useState(false);
  // A card that opened because there was no route yet stays open when the first route is saved, showing it.
  const firstRoute = !!status?.connection && !!status.routing && !status.routing.selection;
  useEffect(() => {if (firstRoute) setRouteOpen(true);}, [firstRoute]);
  const password = useRef<HTMLInputElement>(null);
  const mounted = useRef(false), revision = useRef(0), checking = useRef(false);
  const context = useRef<string|undefined>(undefined);
  function accept(next: Status) {
    if (context.current !== next.context) setActionError('');
    if (context.current !== next.context && password.current) password.current.value = '';
    context.current = next.context;
    checking.current = !!next.busy;
    setError('');
    setStatus(next);
  }
  useEffect(() => {
    mounted.current = true;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const current = revision.current;
      try {
        const next = await local<Status>('openrouter_status');
        if (!alive || current !== revision.current) return;
        accept(next);
      } catch {
        if (alive && current === revision.current) {
          setStatus(undefined);
          if (password.current) password.current.value = '';
          setError(t('Dervo couldn’t check this connection. Reopen settings to try again.'));
        }
      } finally {
        if (alive) timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => {
      mounted.current = false;
      alive = false;
      revision.current++;
      clearTimeout(timer);
      if (password.current) password.current.value = '';
      if (checking.current && context.current) void local('openrouter_cancel', {context: context.current}).catch(() => {});
    };
  }, []);

  async function action(operation: string, args?: Record<string, unknown>) {
    const current = ++revision.current;
    checking.current = operation !== 'openrouter_status';
    setSending(true);
    setError('');
    setActionError('');
    try {
      const next = await local<Status>(operation, operation === 'openrouter_status' ? {} : {...args, context: status?.context});
      if (mounted.current && current === revision.current) {
        accept(next);
      }
    } catch {
      if (mounted.current && current === revision.current) {
        if (operation === 'openrouter_status') {
          setError(t('Dervo couldn’t check this connection. Reopen settings to try again.'));
          return;
        }
        const saving = operation === 'openrouter_route_save' || operation === 'openrouter_budget_save';
        if (!saving) setStatus(previous => previous ? {...previous, connection: null} : previous);
        setActionError(saving ? t('Dervo couldn’t confirm the save. Check your saved settings before trying again.')
          : t('The connection action didn’t finish. Check your Dervo account and try again.'));
      }
    } finally {
      if (mounted.current && current === revision.current) setSending(false);
    }
  }

  function connect(event: FormEvent) {
    event.preventDefault();
    const key = password.current?.value ?? '';
    if (password.current) password.current.value = '';
    void action('openrouter_connect', {key}).then(() => setReplacing(false));
  }

  // Release eligibility comes from the host. No renderer preference enables this section.
  if (!status && error) return <SettingsSection title="OpenRouter" caption={t('Experimental · agents can’t run on it yet')} id="dv-accounts-openrouter">
    <div className="dv-set" role="alert"><div><p className="dv-set-line">{error}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={sending} onClick={() => void action('openrouter_status')}>{t('Retry connection settings')}</button></div></div>
  </SettingsSection>;
  if (!status?.available) return null;
  const connection = status.connection;
  const limits = connection?.limits;
  const needsRoute = !!connection && !!status.routing && !status.routing.selection;
  const left = limits && limits.remainingUsd !== null && limits.limitUsd ? Math.round(limits.remainingUsd / limits.limitUsd * 100) : null;
  const leftText = !limits ? '' : limits.remainingUsd === null ? (limits.limitUsd === null ? t('Not limited') : t('Not reported')) : t('{amount} left', {amount: usd(limits.remainingUsd)});
  const usedText = limits ? usd(limits.usedTodayUsd) : '';
  const keyForm = !connection || replacing;
  // OpenRouter on Connected accounts (settings/connected-accounts-openrouter.html): the key as a row with what it has
  // left, a menu for the rest, and the route and spending limits on one card when you open it.
  return <SettingsSection title="OpenRouter" caption={t('Experimental · agents can’t run on it yet')} id="dv-accounts-openrouter">
    <div aria-label={t('Personal OpenRouter connection')} role="group" className="dv-provider">
      {connection ? <div className="dv-acct" aria-label={t('OpenRouter check result')}>
        <div><p className="dv-acct-name">{t('Inference key')}</p><p className="dv-acct-line">{t('Connection checked')} · {t('{count} models', {count: connection.models.length})}</p>
          <p className="dv-acct-note">{t('Agent execution isn’t enabled here yet.')}</p></div>
        <Meter percent={left} value={leftText} caption={left === null ? t('{leftText} · {usedText} used today',{leftText,usedText}) : t('{usedText} used today on this key',{usedText})}/>
        <DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-icon" aria-label={t('OpenRouter key options')}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="12.5" cy="8" r="1.3"/></svg></button></DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={!status.canCheck || sending || !!status.busy} onSelect={() => void action('openrouter_check')}>{t('Check saved key')}</DropdownMenuItem>
            <DropdownMenuItem disabled={!status.canCheck || sending} onSelect={() => setReplacing(true)}>{t('Replace key')}</DropdownMenuItem>
            {status.routing && <DropdownMenuItem onSelect={() => setRouteOpen(true)}>{t('Route and spending')}</DropdownMenuItem>}
            <DropdownMenuSeparator/>
            <DropdownMenuItem disabled={!status.canCheck || sending || !!status.busy} onSelect={() => void action('openrouter_disconnect')}>{t('Remove saved key')}</DropdownMenuItem>
          </DropdownMenuContent></DropdownMenu>
      </div> : <p role="status" className="dv-sec-note">{status.busy ? t('Checking your key and models…') : status.detail || t('Sign in to Dervo, then check a saved key or add one below.')}</p>}
      {keyForm && <form onSubmit={connect} className="dv-set">
        <div><p className="dv-set-name"><label htmlFor="dv-openrouter-key">{t('Inference key')}</label></p>
          <p id="openrouter-key-help" className="dv-set-line">{t('Dervo saves a verified key in this Mac’s Keychain for your Dervo account. It won’t appear in conversation history.')}</p></div>
        <div className="dv-set-end"><input id="dv-openrouter-key" className="dv-input dv-mono" ref={password} type="password" autoComplete="off" spellCheck={false} required maxLength={265}
          disabled={!status.canCheck || sending} aria-describedby="openrouter-key-help" placeholder={t('Paste your OpenRouter key')}/>
          <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!status.canCheck || sending}>{t('Check and save key')}</button></div>
      </form>}
      {(!connection || status.busy) && <div className="dv-card-actions" style={{justifyContent: 'flex-start'}}>
        {status.busy ? <button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={() => void action('openrouter_cancel')}>{t('Cancel check')}</button>
          : <><button type="button" className="dv-btn dv-btn-quiet" disabled={!status.canCheck || sending} onClick={() => void action('openrouter_check')}>{t('Check saved key')}</button>
            <button type="button" className="dv-btn dv-btn-quiet" disabled={!status.canCheck || sending} onClick={() => void action('openrouter_disconnect')}>{t('Remove saved key')}</button></>}
        {replacing && connection && <button type="button" className="dv-btn dv-btn-quiet" onClick={() => setReplacing(false)}>{t('Keep the saved key')}</button>}
      </div>}
      {connection && (routeOpen || needsRoute) && (status.routing || status.budget) && <OpenCard title={t('Route and spending')} onEscape={() => setRouteOpen(false)}>
        {status.routing && <OpenRouterModels key={status.context} models={connection.models} checked={status.checkedRoutes ?? null} routing={status.routing}
          disabled={sending || !status.canCheck} onCheck={modelId => void action('openrouter_model_check', {modelId})}
          onSave={values => void action('openrouter_route_save', values)}/>}
        {status.budget && <OpenRouterBudget key={`${status.context}:${status.budget.revision}`} budget={status.budget} disabled={sending || !status.canCheck}
          onSave={values => void action('openrouter_budget_save', values)}/>}
        {routeOpen && <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-quiet" onClick={() => setRouteOpen(false)}>{t('Close')}</button></div>}
      </OpenCard>}
      {!connection && status.budget && <OpenRouterBudget key={`${status.context}:${status.budget.revision}`} budget={status.budget} disabled={sending || !status.canCheck}
        onSave={values => void action('openrouter_budget_save', values)}/>}
    </div>
    {(actionError || error) && <p role="alert" className="dv-field-note">{actionError || error}</p>}
  </SettingsSection>;
}
