// Opens Paddle checkout or the customer portal for the signed-in caller, and only for them.
//
// The caller's token is verified with Auth on every request, and their plan, prices and Paddle customer come from
// public.dervo_billing_context() read with that same token, so nobody can buy for, or manage, another account. The
// Paddle API key lives only in this function's secrets. It creates a checkout for one of Dervo's two prices, with the
// caller's account ID as custom data, which is how the webhook knows whose subscription it is. Nothing here grants
// access: only a signed Paddle event does (dervo-billing-webhook).
import { Failure, jsonBody, object, textBody, publishableKeyPattern, reply, supabaseUrlPattern } from '../_shared/http.ts';

export type Config = { supabaseUrl: string; publishableKey: string; paddleApiKey: string; environment: string };
type Dependencies = { fetch: typeof fetch };
const uuid = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const priceId = /^pri_[a-z0-9]{26}$/;
const customerId = /^ctm_[a-z0-9]{26}$/;
const subscriptionId = /^sub_[a-z0-9]{26}$/;
const transactionId = /^txn_[a-z0-9]{26}$/;
const deviceKey = /^[a-f0-9]{64}$/;
// Where Paddle may send people: its own checkout and portal pages, or the default payment link on trydervo.com.
const allowedHosts = /^(?:(?:sandbox-)?(?:checkout|customer-portal|buy|pay)\.paddle\.(?:com|io)|(?:www\.)?trydervo\.com)$/;

export function paddleBase(environment: string): string | null {
  return environment === 'sandbox' ? 'https://sandbox-api.paddle.com' : environment === 'live' ? 'https://api.paddle.com' : null;
}

/** A Paddle key that belongs to the configured environment. Live keys never run against sandbox settings, or back. */
export function keyFits(key: string, environment: string): boolean {
  if (!/^[A-Za-z0-9_]{20,200}$/.test(key)) return false;
  if (key.startsWith('pdl_live_')) return environment === 'live';
  if (key.startsWith('pdl_sdbx_')) return environment === 'sandbox';
  return false;
}

export function safeLink(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !allowedHosts.test(url.hostname)) return null;
    return url.toString();
  } catch { return null; }
}

export function createHandler(config: Config, deps: Dependencies): (request: Request) => Promise<Response> {
  const base = paddleBase(config.environment);
  const validConfig = supabaseUrlPattern.test(config.supabaseUrl) && publishableKeyPattern.test(config.publishableKey)
    && base !== null && keyFits(config.paddleApiKey, config.environment);
  return async (request) => {
    try {
      if (!validConfig || base === null) throw new Failure(503);
      if (request.method !== 'POST') throw new Failure(405);
      const authorization = request.headers.get('Authorization') ?? '';
      if (!/^Bearer [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(authorization)
          || authorization.length > 32775) throw new Failure(401);
      if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw new Failure(400);
      const signal = AbortSignal.any([request.signal, AbortSignal.timeout(15000)]);
      const body = await jsonBody(request, 1024, signal);
      if (!object(body) || typeof body.action !== 'string') throw new Failure(400);
      const keys = Object.keys(body).sort().join();
      // Checkout carries the app's salted hash of this Mac, so a new email on the same Mac doesn't get another trial.
      const checkout = body.action === 'checkout' && keys === 'action,device,interval'
        && (body.interval === 'month' || body.interval === 'year') && typeof body.device === 'string' && deviceKey.test(body.device);
      const manage = body.action === 'manage' && keys === 'action';
      if (!checkout && !manage) throw new Failure(400);

      const headers = { Authorization: authorization, apikey: config.publishableKey, Accept: 'application/json',
        'Content-Type': 'application/json', 'Cache-Control': 'no-cache' };
      async function supabase(path: string, method = 'GET', payload?: string): Promise<unknown> {
        const response = await deps.fetch(config.supabaseUrl + path,
          { method, headers, body: payload, signal, redirect: 'error', cache: 'no-store' });
        if (!response.ok) {
          await response.body?.cancel();
          throw new Failure(response.status === 401 || response.status === 403 ? 403 : 503);
        }
        // A function that returns nothing answers with no body.
        if (response.status === 204 || !response.body) return null;
        const text = await textBody(response, 65536, signal);
        if (text === '') return null;
        try { return JSON.parse(text); } catch { throw new Failure(503); }
      }
      async function paddle(path: string, payload?: unknown, method = 'POST'): Promise<Record<string, unknown>> {
        const response = await deps.fetch(base + path, { method, signal, redirect: 'error', cache: 'no-store',
          headers: { Authorization: 'Bearer ' + config.paddleApiKey, 'Paddle-Version': '1', Accept: 'application/json',
            'Content-Type': 'application/json' },
          body: payload === undefined ? undefined : JSON.stringify(payload) });
        if (!response.ok) { await response.body?.cancel(); throw new Failure(502); }
        const result = await jsonBody(response, 262144, signal);
        if (!object(result) || !object(result.data)) throw new Failure(502);
        return result.data;
      }

      // Verify the user's token with Auth. Never trust decoded JWT claims alone.
      const user = await supabase('/auth/v1/user');
      if (!object(user) || typeof user.id !== 'string' || !uuid.test(user.id)
          || user.is_anonymous !== false || typeof user.email_confirmed_at !== 'string'
          || !Number.isFinite(Date.parse(user.email_confirmed_at))) throw new Failure(403);
      const context = await supabase('/rest/v1/rpc/dervo_billing_context', 'POST',
        JSON.stringify(checkout ? { device: body.device } : {}));
      if (!object(context) || context.user_id !== user.id || context.environment !== config.environment) throw new Failure(503);

      if (checkout) {
        // Nobody pays twice: an open subscription (active, trialing, past due or paused) is managed, not bought again.
        if (context.plan === 'removed' || context.plan === 'paid' || context.open_subscription !== false) throw new Failure(409);
        // The free trial rides on its own prices, offered only to an account that's never had a trial or subscription.
        const trial = context.trial_eligible === true;
        const price = body.interval === 'month'
          ? (trial ? context.month_trial_price_id : context.month_price_id)
          : (trial ? context.year_trial_price_id : context.year_price_id);
        if (typeof price !== 'string' || !priceId.test(price)) throw new Failure(503);
        // One checkout at a time per account, held in the database: a double click, or a second click before Paddle's
        // webhook arrives, reopens the same transaction instead of creating one that could charge again.
        const begun = await supabase('/rest/v1/rpc/dervo_billing_checkout_begin', 'POST',
          JSON.stringify({ billing_interval: body.interval, device: body.device }));
        if (!object(begun)) throw new Failure(503);
        if (begun.state === 'reuse') {
          const url = safeLink(begun.url);
          if (!url) throw new Failure(503);
          return reply(200, { action: 'checkout', url });
        }
        if (begun.state !== 'claimed') throw new Failure(409);
        try {
          // Another interval replaces the open checkout: cancel it in Paddle first, and only if it's this caller's and
          // still unpaid. One that's already paid means the subscription is on its way, so nothing new is sold.
          if (typeof begun.cancel === 'string' && transactionId.test(begun.cancel)) {
            const open = await paddle('/transactions/' + begun.cancel, undefined, 'GET');
            const mine = object(open.custom_data) && open.custom_data.dervo_user_id === user.id;
            if (mine && (open.status === 'draft' || open.status === 'ready')) {
              await paddle('/transactions/' + begun.cancel, { status: 'canceled' }, 'PATCH');
            } else if (mine && open.status !== 'canceled') {
              throw new Failure(409);
            }
          }
          const transaction: Record<string, unknown> = {
            items: [{ price_id: price, quantity: 1 }],
            custom_data: { dervo_user_id: user.id },
            collection_mode: 'automatic',
          };
          if (typeof context.customer_id === 'string' && customerId.test(context.customer_id)) {
            transaction.customer_id = context.customer_id;
          }
          const created = await paddle('/transactions', transaction);
          const url = safeLink(object(created.checkout) ? created.checkout.url : null);
          if (!url || typeof created.id !== 'string' || !transactionId.test(created.id)) throw new Failure(502);
          await supabase('/rest/v1/rpc/dervo_billing_checkout_finish', 'POST', JSON.stringify({ transaction_id: created.id, url }));
          return reply(200, { action: 'checkout', url });
        } catch (error) {
          await supabase('/rest/v1/rpc/dervo_billing_checkout_release', 'POST', '{}').catch(() => {});
          throw error;
        }
      }
      const customer = context.customer_id;
      const subscriptions = context.subscription_ids;
      if (typeof customer !== 'string' || !customerId.test(customer)) throw new Failure(409);
      if (!Array.isArray(subscriptions) || subscriptions.length > 20
          || subscriptions.some((id) => typeof id !== 'string' || !subscriptionId.test(id))) throw new Failure(503);
      const session = await paddle('/customers/' + customer + '/portal-sessions', { subscription_ids: subscriptions });
      const urls = object(session.urls) && object(session.urls.general) ? session.urls.general : null;
      const url = safeLink(urls ? urls.overview : null);
      if (!url) throw new Failure(502);
      return reply(200, { action: 'manage', url });
    } catch (error) {
      return reply(error instanceof Failure ? error.status : 503,
        { error: 'Billing couldn’t be opened. Check your Dervo account and try again.' });
    }
  };
}
