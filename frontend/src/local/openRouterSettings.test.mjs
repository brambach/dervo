import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url: 'http://localhost'});
for (const key of ['window', 'document', 'HTMLElement', 'MutationObserver', 'Node', 'CustomEvent', 'Event']) globalThis[key] = dom.window[key];
Object.defineProperty(globalThis, 'navigator', {configurable: true, value: dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = await import('react');
const {render, fireEvent, screen, waitFor, cleanup, act} = await import('@testing-library/react');
const {OpenRouterSettings} = await import('./OpenRouterSettings.tsx');
after(() => dom.window.close());
afterEach(() => {cleanup(); delete window.__TAURI_INTERNALS__; window.localStorage.clear();});
const blank = {available: true, context: 'activation-one', state: 'unchecked', canCheck: true, busy: false, connection: null};
const key = 'sk-or-' + 'v1-' + 'fixture'.repeat(8);
function mount(handler) {
  const calls = [];
  window.__TAURI_INTERNALS__ = {invoke: async (_, {operation, args}) => {
    calls.push({operation, args}); return handler(operation, args);
  }};
  return {...render(React.createElement(OpenRouterSettings)), calls};
}

test('beta renders no OpenRouter form and only requests host status', async () => {
  const view = mount(() => ({available: false}));
  await act(async () => {});
  assert.equal(view.container.textContent, '');
  assert.deepEqual(view.calls.map(c => c.operation), ['openrouter_status']);
});

test('failed eligibility check is visible and retryable without opening a key form', async () => {
  let fail = true;
  mount(() => {if (fail) throw Error('offline'); return blank;});
  await screen.findByRole('alert');
  assert.equal(screen.queryByLabelText('Inference key'), null);
  fail = false;
  fireEvent.click(screen.getByRole('button', {name: 'Retry connection settings'}));
  await screen.findByLabelText('Inference key');
});

test('automatic status recovery clears its old error', async () => {
  let fail = true;
  mount(() => {if (fail) throw Error('offline'); return blank;});
  await screen.findByRole('alert');
  fail = false;
  await screen.findByLabelText('Inference key', {}, {timeout: 2500});
  assert.equal(screen.queryByRole('alert'), null);
});

test('missing remaining amount does not imply an unlimited key', async () => {
  mount(() => ({...blank, state: 'checked', connection: {limits: {limitUsd: 10, remainingUsd: null, usedTodayUsd: 0}, models: []}}));
  await screen.findByText(/Not reported/);
  assert.equal(screen.queryByText(/Not limited/), null);
});

test('account activation change clears a typed key and stale cleanup carries old context', async () => {
  let value = blank;
  const view = mount(() => value);
  const input = await screen.findByLabelText('Inference key');
  fireEvent.change(input, {target: {value: key}});
  value = {...blank, context: 'signed-out', canCheck: false};
  await waitFor(() => assert.equal(input.value, ''), {timeout: 2500});
  assert.equal(input.disabled, true);
  value = {...blank, context: 'activation-two', busy: true, canCheck: false};
  await screen.findByText('Checking your key and models…', {}, {timeout: 2500});
  view.unmount();
  assert.deepEqual(view.calls.at(-1), {operation: 'openrouter_cancel', args: {context: 'activation-two'}});
});

test('secret clears immediately, is only submitted as a key and never stored as a draft', async () => {
  let finish;
  const view = mount(op => op === 'openrouter_status' ? blank : new Promise(resolve => {finish = resolve;}));
  const input = await screen.findByLabelText('Inference key');
  assert.equal(input.type, 'password');
  fireEvent.change(input, {target: {value: key}});
  fireEvent.submit(input.closest('form'));
  assert.equal(input.value, '');
  assert.equal(window.localStorage.length, 0);
  const request = view.calls.find(c => c.operation === 'openrouter_connect');
  assert.deepEqual(Object.keys(request.args), ['key', 'context']);
  assert.equal(request.args.context, 'activation-one');
  assert.equal(request.args.key, key);
  assert.ok(!view.container.textContent.includes(key));
  await act(async () => {finish({...blank, busy: true, canCheck: false, state: 'checking'});});
  assert.ok(screen.getByRole('button', {name: 'Cancel check'}));
});

test('closing during submission cancels and ignores the late checked response', async () => {
  let finish;
  const view = mount(op => op === 'openrouter_connect' ? new Promise(resolve => {finish = resolve;}) : blank);
  const input = await screen.findByLabelText('Inference key');
  fireEvent.change(input, {target: {value: key}});
  fireEvent.submit(input.closest('form'));
  view.unmount();
  assert.ok(view.calls.some(c => c.operation === 'openrouter_cancel'));
  await act(async () => {finish({...blank, state: 'checked'});});
  assert.equal(view.container.textContent, '');
});

test('raw errors never render provider credentials', async () => {
  mount(op => {if (op === 'openrouter_connect') throw Error(key); return blank;});
  const input = await screen.findByLabelText('Inference key');
  fireEvent.change(input, {target: {value: key}});
  fireEvent.submit(input.closest('form'));
  await screen.findByRole('alert');
  assert.ok(!document.body.textContent.includes(key));
  assert.equal(input.value, '');
});

test('verified usage distinguishes zero from unlimited and offers no paid action', async () => {
  mount(() => ({...blank, state: 'checked', connection: {limits: {limitUsd: null, remainingUsd: null, usedTodayUsd: 0}, models: [{id: 'provider/model'}]}}));
  await screen.findByText('Connection checked · 1 model');
  assert.ok(screen.getByText('Not limited · $0.00 used today'), 'no allowance is "Not limited", and nothing used is $0.00');
  assert.ok(screen.getByText(/Agent execution isn’t enabled here yet/));
  assert.equal(screen.queryByRole('button', {name: /send|run agent/i}), null);
});

test('saved check and removal pass no credential or owner fields', async () => {
  const view = mount(() => blank);
  fireEvent.click(await screen.findByRole('button', {name: 'Check saved key'}));
  await waitFor(() => assert.equal(screen.getByRole('button', {name: 'Remove saved key'}).disabled, false));
  fireEvent.click(screen.getByRole('button', {name: 'Remove saved key'}));
  await act(async () => {});
  assert.deepEqual(view.calls.filter(c => c.operation !== 'openrouter_status'), [
    {operation: 'openrouter_check', args: {context: 'activation-one'}}, {operation: 'openrouter_disconnect', args: {context: 'activation-one'}}
  ]);
});

const emptyBudget = {configured: false, revision: 0, totalUsd: null, perRequestUsd: null,
  usedUsd: '0', reservedUsd: '0', remainingUsd: null, uncertainRequests: 0, costExceededReservation: false,
  scope: 'This Dervo account in this local workspace. Other apps and Macs have separate spending.'};

test('spending limits need an explicit save and carry current account and policy revisions', async () => {
  let status = {...blank, budget: emptyBudget};
  const view = mount((operation, args) => {
    if (operation === 'openrouter_budget_save') status = {...blank, budget: {...emptyBudget, configured: true, revision: 1,
      totalUsd: args.totalUsd, perRequestUsd: args.perRequestUsd, remainingUsd: args.totalUsd}};
    return status;
  });
  const total = await screen.findByLabelText('Workspace budget (USD)');
  const per = screen.getByLabelText('Per request (USD)');
  assert.equal(total.value, '');
  assert.equal(per.value, '');
  fireEvent.change(total, {target: {value: '5.00'}});
  fireEvent.change(per, {target: {value: '0.50'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save limits'}));
  await screen.findByText('Available');
  assert.deepEqual(view.calls.find(c => c.operation === 'openrouter_budget_save').args,
    {context: 'activation-one', revision: 0, totalUsd: '5.00', perRequestUsd: '0.50'});
  assert.ok(view.calls.every(c => ['openrouter_status', 'openrouter_budget_save'].includes(c.operation)));
  assert.ok(screen.getByText(/Saving these limits doesn’t enable paid requests/));
});

test('uncertain charges remain visible as reserved spending', async () => {
  mount(() => ({...blank, budget: {...emptyBudget, configured: true, revision: 1, totalUsd: '5', perRequestUsd: '1',
    reservedUsd: '0.5', remainingUsd: '4.5', uncertainRequests: 1}}));
  await screen.findByText(/1 request has an unconfirmed charge/);
  assert.ok(screen.getByText('Reserved'));
  assert.ok(screen.getByText('$0.5'));
  assert.ok(screen.getByText(/Other apps and Macs have separate spending/));
});

const routeStatus = () => ({...blank, state: 'checked',
  connection: {limits: {limitUsd: 5, remainingUsd: 5, usedTodayUsd: 0}, models: [{id: 'provider/model'}, {id: 'other/model'}]},
  routing: {revision: 0, selection: null},
  checkedRoutes: {modelId: 'provider/model', endpoints: [
    {provider: 'provider', coveredProviders: ['provider','provider/turbo'], contextTokens: 16000, maxOutputTokens: 1024,
      pricing: {prompt:'0.000001',completion:'0.000002',request:'0'}, selectable:true, reason:''},
    {provider: 'unpriced', coveredProviders:['unpriced'], contextTokens: 32000, maxOutputTokens:2048,
      pricing:null, selectable:false, reason:'Pricing needs checking'}]}});

test('model search and explicit provider save carry context without credentials or prices', async () => {
  let status = routeStatus();
  const view = mount((operation,args) => {
    if (operation === 'openrouter_route_save') status = {...status, routing: {revision:1,
      selection: {modelId:args.modelId, provider:args.provider, maxOutputTokens:args.maxOutputTokens, allowFallbacks:false}}};
    return status;
  });
  fireEvent.change(await screen.findByRole('searchbox'), {target:{value:'other'}});
  assert.equal(screen.queryByRole('button', {name:/^provider\/model.*Check providers$/}), null);
  fireEvent.click(screen.getByRole('button', {name:/^other\/model.*Check providers$/}));
  await act(async () => {});
  assert.deepEqual(view.calls.find(c => c.operation === 'openrouter_model_check').args,
    {modelId:'other/model',context:'activation-one'});
  const family = screen.getByRole('button', {name:/^provider.*Includes 2 provider variants/});
  assert.equal(screen.getByRole('button', {name:/^unpriced/}).disabled, true);
  assert.equal(screen.getByRole('button', {name:'Save model and provider'}).disabled, true);
  fireEvent.click(family);
  const tokens = screen.getByLabelText('Maximum reply tokens');
  assert.equal(tokens.max, '1024');
  fireEvent.change(tokens, {target:{value:'512'}});
  fireEvent.click(screen.getByRole('button', {name:'Save model and provider'}));
  await screen.findByLabelText('Saved OpenRouter route');
  assert.deepEqual(view.calls.find(c => c.operation === 'openrouter_route_save').args,
    {context:'activation-one',revision:0,modelId:'provider/model',provider:'provider',maxOutputTokens:512});
});

test('rejected route save retains choices and error across a successful status poll', async () => {
  let fail = true, polls = 0;
  const view = mount(operation => {
    if (operation === 'openrouter_status') polls++;
    if (operation === 'openrouter_route_save' && fail) throw Error(key);
    return routeStatus();
  });
  fireEvent.click(await screen.findByRole('button', {name:/^provider.*Includes/}));
  fireEvent.change(screen.getByLabelText('Maximum reply tokens'), {target:{value:'512'}});
  fireEvent.click(screen.getByRole('button', {name:'Save model and provider'}));
  await screen.findByRole('alert');
  await waitFor(() => assert.ok(polls >= 2), {timeout:2500});
  assert.equal(screen.getByLabelText('Maximum reply tokens').value, '512');
  assert.equal(screen.getByRole('button', {name:/^provider.*Includes/}).getAttribute('aria-pressed'), 'true');
  assert.match(screen.getByRole('alert').textContent, /couldn’t confirm the save/);
  assert.ok(!view.container.textContent.includes(key));
  fail = false;
  fireEvent.click(screen.getByRole('button', {name:'Save model and provider'}));
  await waitFor(() => assert.equal(screen.queryByRole('alert'), null));
});

test('new account activation clears model search and unsaved provider choice', async () => {
  let status = routeStatus();
  mount(() => status);
  fireEvent.change(await screen.findByRole('searchbox'), {target:{value:'other'}});
  fireEvent.click(screen.getByRole('button', {name:/^provider.*Includes/}));
  fireEvent.change(screen.getByLabelText('Maximum reply tokens'), {target:{value:'512'}});
  status = {...routeStatus(),context:'activation-two'};
  await waitFor(() => assert.equal(screen.getByRole('searchbox').value, ''), {timeout:2500});
  assert.equal(screen.getByLabelText('Maximum reply tokens').value, '');
  assert.equal(screen.getByRole('button', {name:'Save model and provider'}).disabled, true);
});
