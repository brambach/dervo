import { createHandler, keyFits, safeLink, type Config } from './handler.ts';
import { createHandler as createWebhook } from '../dervo-billing-webhook/handler.ts';

function assert(value: unknown, message = 'Assertion failed'): asserts value {
  if (!value) throw new Error(message);
}
const cfg: Config = { supabaseUrl: 'https://' + 'a'.repeat(20) + '.supabase.co',
  publishableKey: 'sb_publishable_fixture_not_a_key', paddleApiKey: 'pdl_sdbx_apikey_fixture_not_a_key', environment: 'sandbox' };
const token = 'fixture.payload.signature';
const user = { id: '00000000-0000-0000-0000-000000000001', is_anonymous: false, email_confirmed_at: '2026-01-01T00:00:00Z' };
const month = 'pri_' + 'm'.repeat(26), year = 'pri_' + 'y'.repeat(26);
const monthTrial = 'pri_' + 'n'.repeat(26), yearTrial = 'pri_' + 'z'.repeat(26);
const customer = 'ctm_' + 'c'.repeat(26), subscription = 'sub_' + 's'.repeat(26);
const device = 'd'.repeat(64), txn = 'txn_' + 't'.repeat(26), oldTxn = 'txn_' + 'o'.repeat(26);
const claimed = (cancel: string | null = null) => json({ state: 'claimed', cancel });
const done = () => new Response(null, { status: 204 });
const created = (url: string, id = txn) => json({ data: { id, checkout: { url } } });
const context = (extra: Record<string, unknown> = {}) => ({ user_id: user.id, plan: 'trial', environment: 'sandbox',
  month_price_id: month, year_price_id: year, month_trial_price_id: monthTrial, year_trial_price_id: yearTrial,
  trial_eligible: false, open_subscription: false, customer_id: null, subscription_ids: [], ...extra });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const request = (body: unknown = { action: 'checkout', interval: 'year', device }, auth = 'Bearer ' + token) =>
  new Request('https://fixture.invalid/billing', { method: 'POST',
    headers: { Authorization: auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function fixture(responses: Response[], config = cfg) {
  const calls: { url: string; init: RequestInit; body: string }[] = [];
  const fakeFetch: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), init: init ?? {}, body: String(init?.body ?? '') });
    assert(init?.redirect === 'error' && init.cache === 'no-store');
    const next = responses.shift();
    assert(next, 'Unexpected request');
    return next;
  };
  return { handler: createHandler(config, { fetch: fakeFetch }), calls };
}

Deno.test('checkout is for the caller, at one of Dervo\'s prices, in sandbox', async () => {
  const { handler, calls } = fixture([json(user), json(context()), claimed(),
    created('https://trydervo.com/checkout?_ptxn=txn_1'), done()]);
  const response = await handler(request());
  assert(response.status === 200);
  const body = await response.json();
  assert(body.url === 'https://trydervo.com/checkout?_ptxn=txn_1' && body.action === 'checkout');
  assert(calls[0].url === cfg.supabaseUrl + '/auth/v1/user');
  assert(calls[1].url === cfg.supabaseUrl + '/rest/v1/rpc/dervo_billing_context');
  assert(JSON.parse(calls[1].body).device === device, 'eligibility is checked for this Mac');
  assert((calls[1].init.headers as Record<string, string>).Authorization === 'Bearer ' + token, 'context read as the caller');
  assert(calls[2].url === cfg.supabaseUrl + '/rest/v1/rpc/dervo_billing_checkout_begin');
  assert(JSON.parse(calls[2].body).billing_interval === 'year' && JSON.parse(calls[2].body).device === device);
  assert(calls[3].url === 'https://sandbox-api.paddle.com/transactions');
  const sent = JSON.parse(calls[3].body);
  assert(sent.items[0].price_id === year && sent.custom_data.dervo_user_id === user.id && !('customer_id' in sent));
  assert((calls[3].init.headers as Record<string, string>).Authorization === 'Bearer ' + cfg.paddleApiKey);
  assert(calls[4].url === cfg.supabaseUrl + '/rest/v1/rpc/dervo_billing_checkout_finish');
  assert(JSON.parse(calls[4].body).transaction_id === txn);
  assert(!JSON.stringify(body).includes(cfg.paddleApiKey));
});

Deno.test('a returning customer checks out as the same Paddle customer', async () => {
  const { handler, calls } = fixture([json(user), json(context({ plan: 'none', customer_id: customer })), claimed(),
    created('https://sandbox-checkout.paddle.io/x'), done()]);
  const response = await handler(request({ action: 'checkout', interval: 'month', device }));
  assert(response.status === 200);
  const sent = JSON.parse(calls[3].body);
  assert(sent.customer_id === customer && sent.items[0].price_id === month);
});

Deno.test('paid, removed and still-open accounts can\'t check out, and nothing reaches Paddle', async () => {
  for (const extra of [{ plan: 'paid' }, { plan: 'removed' }, { plan: 'none', open_subscription: true },
    { plan: 'none', open_subscription: undefined }]) {
    const { handler, calls } = fixture([json(user), json(context(extra))]);
    assert((await handler(request())).status === 409, JSON.stringify(extra));
    assert(calls.length === 2);
  }
});

Deno.test('an account that has never had a trial checks out on the trial price', async () => {
  const { handler, calls } = fixture([json(user), json(context({ plan: 'none', trial_eligible: true })), claimed(),
    created('https://trydervo.com/checkout?_ptxn=txn_2'), done()]);
  assert((await handler(request({ action: 'checkout', interval: 'month', device }))).status === 200);
  assert(JSON.parse(calls[3].body).items[0].price_id === monthTrial);
});

Deno.test('a double click reopens the open checkout, and a click while one is being made waits', async () => {
  const again = fixture([json(user), json(context()), json({ state: 'reuse', transaction_id: txn, url: 'https://trydervo.com/checkout?_ptxn=1' })]);
  const response = await again.handler(request());
  assert(response.status === 200 && (await response.json()).url === 'https://trydervo.com/checkout?_ptxn=1');
  assert(again.calls.length === 3, 'nothing new reaches Paddle');
  const busy = fixture([json(user), json(context()), json({ state: 'busy' })]);
  assert((await busy.handler(request())).status === 409 && busy.calls.length === 3);
  const poisoned = fixture([json(user), json(context()), json({ state: 'reuse', url: 'https://evil.example/' })]);
  assert((await poisoned.handler(request())).status === 503);
});

Deno.test('another interval cancels the caller\'s unpaid checkout in Paddle first', async () => {
  const { handler, calls } = fixture([json(user), json(context()), claimed(oldTxn),
    json({ data: { id: oldTxn, status: 'ready', custom_data: { dervo_user_id: user.id } } }), json({ data: { id: oldTxn } }),
    created('https://trydervo.com/checkout?_ptxn=2'), done()]);
  assert((await handler(request())).status === 200);
  assert(calls[3].url === `https://sandbox-api.paddle.com/transactions/${oldTxn}` && calls[3].init.method === 'GET');
  assert(calls[4].init.method === 'PATCH' && JSON.parse(calls[4].body).status === 'canceled');
  assert(calls[5].url === 'https://sandbox-api.paddle.com/transactions');
});

Deno.test('a paid open checkout sells nothing more, and someone else\'s is never cancelled', async () => {
  const paid = fixture([json(user), json(context()), claimed(oldTxn),
    json({ data: { id: oldTxn, status: 'completed', custom_data: { dervo_user_id: user.id } } }), done()]);
  assert((await paid.handler(request())).status === 409);
  assert(paid.calls[4].url === cfg.supabaseUrl + '/rest/v1/rpc/dervo_billing_checkout_release', 'the claim is released');
  assert(!paid.calls.some((c) => c.init.method === 'PATCH' || c.url.endsWith('/transactions')));
  const other = fixture([json(user), json(context()), claimed(oldTxn),
    json({ data: { id: oldTxn, status: 'ready', custom_data: { dervo_user_id: '00000000-0000-0000-0000-000000000009' } } }),
    created('https://trydervo.com/checkout?_ptxn=3'), done()]);
  assert((await other.handler(request())).status === 200);
  assert(!other.calls.some((c) => c.init.method === 'PATCH'));
});

Deno.test('manage opens the caller\'s own portal', async () => {
  const { handler, calls } = fixture([json(user), json(context({ plan: 'paid', customer_id: customer, subscription_ids: [subscription] })),
    json({ data: { urls: { general: { overview: 'https://sandbox-customer-portal.paddle.com/cpl_1?token=t' } } } })]);
  const response = await handler(request({ action: 'manage' }));
  assert(response.status === 200);
  assert((await response.json()).url.startsWith('https://sandbox-customer-portal.paddle.com/'));
  assert(calls[2].url === `https://sandbox-api.paddle.com/customers/${customer}/portal-sessions`);
  assert(JSON.parse(calls[2].body).subscription_ids[0] === subscription);
});

Deno.test('manage without a customer is refused', async () => {
  const { handler } = fixture([json(user), json(context())]);
  assert((await handler(request({ action: 'manage' }))).status === 409);
});

Deno.test('bad requests, tokens, users and contexts are refused before Paddle', async () => {
  for (const body of [{ action: 'checkout' }, { action: 'checkout', interval: 'week', device }, { action: 'manage', interval: 'month' },
    { action: 'refund' }, { action: 'checkout', interval: 'month', device, price_id: month }, { action: 'checkout', interval: 'month' },
    { action: 'checkout', interval: 'month', device: 'D'.repeat(64) }, { action: 'manage', device }]) {
    const { handler, calls } = fixture([]);
    assert((await handler(request(body))).status === 400, JSON.stringify(body));
    assert(calls.length === 0);
  }
  assert((await fixture([]).handler(request(undefined, 'Bearer nope'))).status === 401);
  assert((await fixture([]).handler(new Request('https://fixture.invalid/billing'))).status === 405);
  assert((await fixture([json({ ...user, is_anonymous: true })]).handler(request())).status === 403);
  assert((await fixture([json({}, 401)]).handler(request())).status === 403);
  assert((await fixture([json(user), json(context({ user_id: '00000000-0000-0000-0000-000000000002' }))]).handler(request())).status === 503);
  assert((await fixture([json(user), json(context({ environment: 'live' }))]).handler(request())).status === 503);
  assert((await fixture([json(user), json(context({ year_price_id: null }))]).handler(request())).status === 503);
});

Deno.test('a link Paddle returns must be https on Paddle or trydervo.com', async () => {
  for (const url of ['http://trydervo.com/checkout', 'https://evil.example/checkout', 'https://trydervo.com.evil.example/',
    'https://user:pw@trydervo.com/', 'javascript:alert(1)', 'https://trydervo.com:8443/']) {
    assert(safeLink(url) === null, url);
    const { handler, calls } = fixture([json(user), json(context()), claimed(), created(url), done()]);
    assert((await handler(request())).status === 502, url);
    assert(calls[4].url.endsWith('/rpc/dervo_billing_checkout_release'), 'a failed checkout releases its claim');
  }
  assert(safeLink('https://checkout.paddle.com/x') && safeLink('https://www.trydervo.com/checkout?_ptxn=1'));
});

Deno.test('a key from the other environment, or a bad config, refuses everything', async () => {
  assert(keyFits('pdl_sdbx_apikey_fixture_not_a_key', 'sandbox') && !keyFits('pdl_sdbx_apikey_fixture_not_a_key', 'live'));
  assert(keyFits('pdl_live_apikey_fixture_not_a_key', 'live') && !keyFits('pdl_live_apikey_fixture_not_a_key', 'sandbox'));
  for (const config of [{ ...cfg, environment: 'live' }, { ...cfg, environment: '' }, { ...cfg, paddleApiKey: '' },
    { ...cfg, supabaseUrl: 'https://example.com' }]) {
    const { handler, calls } = fixture([], config);
    assert((await handler(request())).status === 503);
    assert(calls.length === 0);
  }
});

Deno.test('a Paddle error isn\'t echoed', async () => {
  const { handler } = fixture([json(user), json(context()), claimed(), json({ error: { detail: 'secret detail' } }, 400), done()]);
  const response = await handler(request());
  assert(response.status === 502 && !(await response.text()).includes('secret'));
});

// The webhook forwarder: raw body and signature through, nothing decided here.
const hook = { supabaseUrl: cfg.supabaseUrl, publishableKey: cfg.publishableKey };
function webhook(responses: Response[]) {
  const calls: { url: string; init: RequestInit }[] = [];
  const handler = createWebhook(hook, { fetch: async (input, init) => {
    calls.push({ url: String(input), init: init ?? {} });
    const next = responses.shift(); assert(next, 'Unexpected request'); return next;
  } });
  return { handler, calls };
}
const delivery = (body: string, signature: string | null = 'ts=1;h1=' + 'a'.repeat(64)) =>
  new Request('https://fixture.invalid/hook', { method: 'POST', body,
    headers: signature === null ? {} : { 'Paddle-Signature': signature } });

Deno.test('the webhook forwards the exact body and signature with only the publishable key', async () => {
  const raw = '{"event_id":"evt_x", "note":"spacing and ünïcode kept"}';
  const { handler, calls } = webhook([json({ state: 'applied' })]);
  const response = await handler(delivery(raw));
  assert(response.status === 200 && (await response.json()).state === 'applied');
  assert(calls[0].url === cfg.supabaseUrl + '/rest/v1/rpc/dervo_billing_ingest');
  const sent = JSON.parse(String(calls[0].init.body));
  assert(sent.payload === raw && sent.signature === 'ts=1;h1=' + 'a'.repeat(64));
  const headers = calls[0].init.headers as Record<string, string>;
  assert(headers.apikey === cfg.publishableKey && !('Authorization' in headers));
});

Deno.test('the webhook refuses unsigned, oversized or non-POST deliveries and passes on rejections', async () => {
  assert((await webhook([]).handler(delivery('{}', null))).status === 401);
  assert((await webhook([]).handler(delivery('{}', 'ts=1;h1=<script>'))).status === 401);
  assert((await webhook([]).handler(delivery('x'.repeat(262145)))).status === 413);
  assert((await webhook([]).handler(new Request('https://fixture.invalid/hook'))).status === 405);
  assert((await webhook([json({ message: 'Billing signature rejected' }, 403)]).handler(delivery('{}'))).status === 401);
  assert((await webhook([json({}, 400)]).handler(delivery('{}'))).status === 400);
  assert((await webhook([json({}, 500)]).handler(delivery('{}'))).status === 503);
  const bad = createWebhook({ ...hook, publishableKey: '' }, { fetch: () => { throw new Error('no'); } });
  assert((await bad(delivery('{}'))).status === 503);
});
