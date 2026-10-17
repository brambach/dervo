import { useEffect, useRef, useState } from 'react';
import { MessageSquare, X } from 'lucide-react';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/shared/ui/dialog';
import { betaFeedbackContext, sendBetaFeedback, type BetaFeedbackCategory, type BetaFeedbackContext, type BetaFeedbackRequest } from './api';
import './BetaFeedback.css';
import { Segmented } from './DervoControls';
import { Disclosure } from './SettingsKit';
import { StatusGlyph } from './StatusGlyph';
import {formatNumber,t} from '@/i18n';

const categories: {value: BetaFeedbackCategory; label: string}[] = [
  {value: 'feedback', label: t('Feedback')}, {value: 'bug', label: t('Bug')}, {value: 'confusing', label: t('Confusing')}, {value: 'idea', label: t('Idea')},
];
const placeholders: Record<BetaFeedbackCategory, string> = {
  feedback: t('Tell us what’s on your mind, including what you like.'),
  bug: t('What did you do, what did you expect, and what happened instead?'),
  confusing: t('What were you trying to do, and where did it stop making sense?'),
  idea: t('What would make Dervo more useful for you?'),
};
const failure = t('Feedback wasn’t confirmed. Your draft is kept. Check your account and connection, then retry.');
/** The host's own line when it refused the note and said why (the hourly limit: "You've sent a lot of feedback this
 * hour…"), from an error result's ``detail``. Anything else, including a thrown error's own words, is the fixed line. */
export function feedbackProblem(error: unknown): string {
  const detail = error && typeof error === 'object' ? (error as {detail?: unknown}).detail : null;
  return typeof detail === 'string' && detail.trim() ? detail.trim().slice(0, 300) : failure;
}

/** Explicit capture avoids spreading unrelated state, titles, errors or conversation text. */
export function feedbackContext(context: BetaFeedbackContext): BetaFeedbackContext {
  const result: BetaFeedbackContext = {route: context.route};
  for (const key of ['projectId', 'taskId', 'conversationId', 'sessionId'] as const) {
    const value = context[key];
    if (value && /^[A-Za-z0-9_:-]{1,128}$/.test(value)) result[key] = value;
  }
  if (context.provider && ['codex', 'claude', 'openrouter'].includes(context.provider)) result.provider = context.provider;
  if (context.appVersion && /^[A-Za-z0-9.+_-]{1,80}$/.test(context.appVersion)) result.appVersion = context.appVersion;
  if (typeof window !== 'undefined') result.viewport = {width: Math.max(1, Math.min(32768, Math.round(window.innerWidth))), height: Math.max(1, Math.min(32768, Math.round(window.innerHeight)))};
  return result;
}

/** ``requestOpen`` lets the account menu, ⌘K and Settings open the dialog; ``launcher`` false leaves out the sidebar button.
 * ``view`` names the screen it was opened over ("Home in dervo app"), for the line that says what's sent with it.
 * ``initialCategory`` picks the kind each time a request opens it: ⌘⇧B and /bug open it on Bug for a tester. Without
 * one, the kind stays as you left it, with your draft. */
export function BetaFeedback({context, view, send = sendBetaFeedback, getContext = betaFeedbackContext, requestOpen = 0, launcher = true, initialCategory}: {context: BetaFeedbackContext; view?: string; send?: typeof sendBetaFeedback; getContext?: typeof betaFeedbackContext; requestOpen?: number; launcher?: boolean; initialCategory?: BetaFeedbackCategory}) {
  const [open, setOpen] = useState(false);
  const returnFocus=useRef<HTMLElement|null>(null);
  const [category, setCategory] = useState<BetaFeedbackCategory>(initialCategory ?? 'feedback');
  useEffect(() => { if (requestOpen) {returnFocus.current=document.activeElement instanceof HTMLElement?document.activeElement:null;if (initialCategory) setCategory(initialCategory);setOpen(true);} }, [requestOpen]);
  const [contextId, setContextId] = useState<string | null>(null);
  const [ownerKey, setOwnerKey] = useState<string | null>(null);
  const [appVersion, setAppVersion] = useState<string | undefined>();
  const [message, setMessage] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [problem, setProblem] = useState(failure);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pending = useRef<BetaFeedbackRequest | null>(null);
  const pendingOwner = useRef<string | null>(null);
  const busy = useRef(false);
  const mounted = useRef(true);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; if (closeTimer.current) clearTimeout(closeTimer.current); }; }, []);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setContextId(null);
    void getContext().then(result => {
      if (current) { setContextId(result.contextId); setOwnerKey(result.ownerKey); setAppVersion(result.appVersion ?? undefined); }
    }).catch(() => { if (current) { setProblem(failure); setState('error'); } });
    return () => { current = false; };
  }, [open, getContext]);

  async function submit() {
    if (busy.current || !contextId || !ownerKey || !message.trim() || state === 'sent') return;
    const previous = pending.current;
    if (previous && pendingOwner.current !== ownerKey) { setProblem(failure); setState('error'); return; }
    busy.current = true;
    setState('sending');
    const request = previous?.category === category && previous.message === message ? {...previous, contextId} : {
      contextId, requestId: crypto.randomUUID(), category, message, context: feedbackContext({...context, appVersion}),
    };
    pending.current = request;
    pendingOwner.current = ownerKey;
    try {
      const result = await send(request);
      if (result.state !== 'sent' || result.receipt?.id !== request.requestId) throw Object.assign(new Error(failure), {detail: result.state === 'error' ? result.detail : null});
      if (!mounted.current) return;
      setState('sent'); setMessage(''); pending.current = null;
      closeTimer.current = setTimeout(() => { setOpen(false); setState('idle'); }, 2000);
    } catch (error) {
      if (mounted.current) { setProblem(feedbackProblem(error)); setState('error'); }
    } finally { busy.current = false; }
  }
  function changeOpen(next: boolean) {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
    if (state === 'sent') setState('idle');
    setOpen(next);
  }

  const count = message.length;
  const disabled = state === 'sending' || state === 'sent';
  // Feedback is a dialog over whatever you're looking at (settings/feedback-b.html), so it can say exactly what it sends.
  return <Dialog open={open} onOpenChange={changeOpen}>
    {launcher && <DialogTrigger asChild><button type="button" className="dervo-feedback-launcher"><MessageSquare size={16} aria-hidden="true" />{t('Feedback')}</button></DialogTrigger>}
    <DialogContent className="dervo-feedback-dialog dv-feedback" showCloseButton={false} onCloseAutoFocus={event=>{if(returnFocus.current?.isConnected){event.preventDefault();returnFocus.current.focus();}}} onOpenAutoFocus={event => { event.preventDefault(); textarea.current?.focus(); }}>
      <div className="dv-feedback-head"><div><DialogTitle>{t('Send feedback')}</DialogTitle>
        <DialogDescription>{t('Send a note to the Dervo team. You can also open this form with /feedback, or /bug for a bug.')}</DialogDescription></div>
        <DialogClose asChild><button type="button" className="dv-btn dv-btn-icon" aria-label={t('Close')}><X size={16} aria-hidden="true" /></button></DialogClose></div>
      {state === 'sent' ? <div className="dv-feedback-sent" role="status"><StatusGlyph state="done" /><div><b>{t('Thanks, it’s sent.')}</b>
        <span className="dv-caption">{t('Your note reached the Dervo team. This closes in a moment.')}</span></div></div>
      : <form onSubmit={event => { event.preventDefault(); void submit(); }} onKeyDown={event => {
        if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void submit(); }
      }}>
        <Segmented label={t('Kind of feedback')} value={category} onChange={setCategory} disabled={disabled} options={categories} />
        <label className="sr-only" htmlFor="dervo-beta-feedback-message">{t('Your feedback')}</label>
        <textarea id="dervo-beta-feedback-message" ref={textarea} className="dv-input" value={message} onChange={event => { setMessage(event.target.value); if (state === 'error') setState('idle'); }} maxLength={4000} disabled={disabled} placeholder={placeholders[category]} rows={5} />
        {count >= 3500 && <p className="dv-field-note" aria-live="polite">{t('{count} of {limit} characters', {count: formatNumber(count), limit: formatNumber(4000)})}</p>}
        <Disclosure summary={t('Sent with it: {items}', {items: [view, t('your account'), appVersion ? `Dervo ${appVersion}` : ''].filter(Boolean).join(', ')})}>
          {t('Plus the window size')}{context.projectId ? ` ${t('and the project ID')}` : ''}{context.taskId || context.conversationId ? t(', and the thread or task ID') : ''}. {t('Threads, messages and files aren’t attached. Leave out secrets.')}
        </Disclosure>
        {state === 'error' && <p role="alert" className="dv-field-note">{problem}</p>}
        <div className="dv-feedback-foot">
          <span className="dv-caption">{!contextId || !ownerKey ? t('Getting ready to send…') : t('⌘ Return to send')}</span>
          <DialogClose asChild><button type="button" className="dv-btn dv-btn-quiet">{state === 'sending' ? t('Close') : t('Cancel')}</button></DialogClose>
          <button type="submit" className="dv-btn dv-btn-primary" disabled={!contextId || !ownerKey || !message.trim() || state === 'sending'}>{state === 'sending' ? t('Sending…') : t('Send feedback')}</button>
        </div>
      </form>}
    </DialogContent>
  </Dialog>;
}
