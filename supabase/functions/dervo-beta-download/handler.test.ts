import { AwsClient } from 'npm:aws4fetch@1.0.20';
import { createHandler, type Config, type Signer } from './handler.ts';

function assert(value: unknown, message = 'Assertion failed'): asserts value {
  if (!value) throw new Error(message);
}
const cfg: Config = { supabaseUrl: 'https://' + 'a'.repeat(20) + '.supabase.co',
  publishableKey: 'sb_publishable_fixture_not_a_key', r2Account: 'a'.repeat(32) };
const token = 'fixture.payload.signature';
const hash = 'a'.repeat(64);
const now = Date.UTC(2026, 8, 15, 10);
const origin = 'https://' + cfg.r2Account + '.r2.cloudflarestorage.com';
const artifact = { release_id: 'trial.10', platform: 'macos-arm64', object_name: 'trial.10/Dervo-macos-arm64.zip',
  download_origin: origin, sha256: hash, bytes: 300000000, format: 'zip' };
const user = { id: '00000000-0000-0000-0000-000000000001', is_anonymous: false,
  email_confirmed_at: '2026-01-01T00:00:00Z' };
const access = { status: 'approved', allowed: true, release_channel: 'private-beta', expires_at: null };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const head = () => new Response(null, { headers: { 'content-length': String(artifact.bytes), 'x-amz-meta-sha256': hash } });
const request = (body: unknown = { releaseId: artifact.release_id, sha256: hash }) =>
  new Request('https://fixture.invalid/download', { method: 'POST',
    headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function fixture(responses: (Response | Error)[] = [json(user), json(access), json([artifact]), head(), json([artifact])], config = cfg) {
  const calls: Request[] = [], signs: { request: Request; query: boolean }[] = [];
  const aws = new AwsClient({ accessKeyId: 'b'.repeat(32), secretAccessKey: 'c'.repeat(64), service: 's3', region: 'auto' });
  const sign: Signer = async (req, query) => {
    signs.push({ request: req, query });
    return aws.sign(req, { aws: { signQuery: query, datetime: '20260915T100000Z' } });
  };
  const fakeFetch: typeof fetch = async (input, init) => {
    const req = new Request(input, init);
    calls.push(req);
    assert(req.redirect === 'error');
    // Deno doesn't expose Request.cache. Check the supplied fetch option.
    assert(init && 'cache' in init && init.cache === 'no-store');
    const next = responses.shift();
    if (next instanceof Error) throw next;
    assert(next, 'Unexpected request');
    return next;
  };
  return { handler: createHandler(config, { fetch: fakeFetch, sign, now: () => now }), calls, signs };
}

Deno.test('approved download uses real signing library with fake keys and no network', async () => {
  const f = fixture();
  const response = await f.handler(request());
  assert(response.status === 200);
  assert(response.headers.get('cache-control') === 'no-store');
  const body = await response.json();
  assert(body.releaseId === artifact.release_id && body.bytes === artifact.bytes && body.sha256 === hash);
  assert(body.expiresAt === now / 1000 + 120);
  const url = new URL(body.url);
  assert(url.origin === origin && url.pathname === '/dervo-beta-releases/' + artifact.object_name);
  assert(url.searchParams.get('X-Amz-Expires') === '120');
  assert(url.searchParams.get('X-Amz-SignedHeaders') === 'host');
  assert(url.searchParams.get('response-content-disposition') === 'attachment; filename="Dervo-trial.10.zip"');
  assert(/^[a-f0-9]{64}$/.test(url.searchParams.get('X-Amz-Signature')!));
  assert(!body.url.includes(token) && !body.url.includes(cfg.publishableKey));
  assert(f.calls.length === 5 && f.signs.length === 2);
  assert(f.calls[3].method === 'HEAD');
  assert(f.calls[3].headers.get('authorization')!.startsWith('AWS4-HMAC-SHA256'));
  for (const call of f.calls.filter((c) => c.url.startsWith(cfg.supabaseUrl))) {
    assert(call.headers.get('authorization') === 'Bearer ' + token);
    assert(!call.url.includes(token));
  }
  assert(f.signs[1].query && f.signs[1].request.method === 'GET');
});

Deno.test('missing authentication, invalid request and bad config never call services', async () => {
  for (const req of [new Request('https://fixture.invalid'), request({ releaseId: '../x', sha256: hash }),
      request({ releaseId: 'trial.10', sha256: hash, url: 'https://evil.invalid' }), request({}), request('x'.repeat(1200))]) {
    const f = fixture([]); assert((await f.handler(req)).status >= 400); assert(f.calls.length === 0);
  }
  const req = request(); req.headers.set('Authorization', 'Bearer not-a-jwt');
  const f = fixture([]); assert((await f.handler(req)).status === 401); assert(f.calls.length === 0);
  const invalid = fixture([], { ...cfg, supabaseUrl: 'https://evil.invalid' });
  assert((await invalid.handler(request())).status === 503 && invalid.calls.length === 0);
});

Deno.test('forged, unconfirmed and anonymous identities cannot ask R2 for anything', async () => {
  for (const first of [json({}, 401), json({ ...user, is_anonymous: true }), json({ ...user, email_confirmed_at: null }),
      json({ ...user, id: 'bad' }), new Error('upstream-secret')]) {
    const f = fixture([first]); const response = await f.handler(request());
    assert(response.status >= 400 && f.calls.length === 1 && f.signs.length === 0);
    assert(!(await response.text()).includes('upstream-secret'));
  }
});

Deno.test('pending, revoked, expired and inconsistent memberships are denied', async () => {
  for (const changes of [{ status: 'pending', allowed: false }, { status: 'revoked', allowed: false },
      { status: 'revoked', allowed: true }, { expires_at: new Date(now).toISOString() },
      { expires_at: 'bad' }, { release_channel: 'personal-experimental' }]) {
    const f = fixture([json(user), json({ ...access, ...changes })]);
    assert((await f.handler(request())).status === 403 && f.signs.length === 0 && f.calls.length === 2);
  }
});

Deno.test('unpublished or mismatched metadata cannot sign arbitrary objects', async () => {
  const rows = [[], [artifact, artifact], [{ ...artifact, download_origin: 'https://evil.invalid' }],
    [{ ...artifact, object_name: '../secrets' }], [{ ...artifact, sha256: 'b'.repeat(64) }],
    [{ ...artifact, bytes: 0 }], [{ ...artifact, bytes: 2147483649 }], [{ ...artifact, extra: true }]];
  for (const row of rows) {
    const f = fixture([json(user), json(access), json(row)]);
    assert((await f.handler(request())).status === 403 && f.signs.length === 0);
  }
});

Deno.test('a Windows row gets a link to its own installer', async () => {
  const windows = { ...artifact, release_id: 'trial.30-windows', platform: 'windows-x64', format: 'exe',
    object_name: 'trial.30-windows/Dervo-windows-x64.exe' };
  const f = fixture([json(user), json(access), json([windows]), head(), json([windows])]);
  const response = await f.handler(request({ releaseId: windows.release_id, sha256: hash }));
  assert(response.status === 200);
  const url = new URL((await response.json()).url);
  assert(url.pathname === '/dervo-beta-releases/' + windows.object_name);
  assert(url.searchParams.get('response-content-disposition') === 'attachment; filename="Dervo-trial.30-windows.exe"');
});

Deno.test('a Linux row gets a link to its own package', async () => {
  for (const format of ['deb', 'rpm', 'appimage']) {
    const id = 'trial.40-linux-' + format;
    const linux = { ...artifact, release_id: id, platform: 'linux-x64', format, object_name: `${id}/Dervo-linux-x64.${format}` };
    const f = fixture([json(user), json(access), json([linux]), head(), json([linux])]);
    const response = await f.handler(request({ releaseId: id, sha256: hash }));
    assert(response.status === 200);
    const url = new URL((await response.json()).url);
    assert(url.pathname === '/dervo-beta-releases/' + linux.object_name);
  }
});

Deno.test('Linux rows whose id, format or platform disagree cannot sign anything', async () => {
  const linux = { ...artifact, release_id: 'trial.40-linux-deb', platform: 'linux-x64', format: 'deb',
    object_name: 'trial.40-linux-deb/Dervo-linux-x64.deb' };
  const cases: [string, Record<string, unknown>][] = [
    ['trial.40-linux-rpm', { ...linux, release_id: 'trial.40-linux-rpm', object_name: 'trial.40-linux-rpm/Dervo-linux-x64.deb' }],
    ['trial.40-linux', { ...linux, release_id: 'trial.40-linux', object_name: 'trial.40-linux/Dervo-linux-x64.deb' }],
    [linux.release_id, { ...linux, format: 'exe', object_name: 'trial.40-linux-deb/Dervo-linux-x64.exe' }],
    ['trial.40-linux-deb', { ...artifact, release_id: 'trial.40-linux-deb', object_name: 'trial.40-linux-deb/Dervo-macos-arm64.zip' }],
  ];
  for (const [releaseId, row] of cases) {
    const f = fixture([json(user), json(access), json([row])]);
    assert((await f.handler(request({ releaseId, sha256: hash }))).status === 403 && f.signs.length === 0);
  }
});

Deno.test('rows that mix up platforms, formats and release ids cannot sign anything', async () => {
  const windows = { ...artifact, release_id: 'trial.30-windows', platform: 'windows-x64', format: 'exe',
    object_name: 'trial.30-windows/Dervo-windows-x64.exe' };
  const cases: [string, Record<string, unknown>][] = [
    [artifact.release_id, { ...artifact, platform: 'windows-x64' }],
    [artifact.release_id, { ...artifact, format: 'exe', object_name: 'trial.10/Dervo-macos-arm64.exe' }],
    [artifact.release_id, { ...artifact, platform: 'linux-x64', object_name: 'trial.10/Dervo-linux-x64.zip' }],
    [artifact.release_id, { ...artifact, platform: 'constructor', object_name: 'trial.10/Dervo-constructor.zip' }],
    [windows.release_id, { ...windows, format: 'zip', object_name: 'trial.30-windows/Dervo-windows-x64.zip' }],
    [windows.release_id, { ...windows, object_name: 'trial.30-windows/Dervo-macos-arm64.exe' }],
    ['trial.30-windows', { ...artifact, release_id: 'trial.30-windows', object_name: 'trial.30-windows/Dervo-macos-arm64.zip' }],
    ['trial.30', { ...windows, release_id: 'trial.30', object_name: 'trial.30/Dervo-windows-x64.exe' }],
  ];
  for (const [releaseId, row] of cases) {
    const f = fixture([json(user), json(access), json([row])]);
    assert((await f.handler(request({ releaseId, sha256: hash }))).status === 403 && f.signs.length === 0);
  }
});

Deno.test('missing object, size or hash mismatch prevents the downloadable signature', async () => {
  for (const h of [new Response(null, { status: 404 }), new Response(null),
      new Response(null, { headers: { 'content-length': '100', 'x-amz-meta-sha256': hash } }),
      new Response(null, { headers: { 'content-length': String(artifact.bytes), 'x-amz-meta-sha256': 'b'.repeat(64) } })]) {
    const f = fixture([json(user), json(access), json([artifact]), h]);
    assert((await f.handler(request())).status === 503 && f.signs.length === 1 && !f.signs[0].query);
  }
});

Deno.test('revocation or withdrawal during HEAD prevents a fresh link', async () => {
  for (const final of [json([]), json({}, 403), json([{ ...artifact, bytes: artifact.bytes + 1 }])]) {
    const f = fixture([json(user), json(access), json([artifact]), head(), final]);
    assert((await f.handler(request())).status >= 400 && f.signs.length === 1);
  }
});

Deno.test('unchanged metadata allows a download regardless of JSON property order', async () => {
  const reordered = Object.fromEntries(Object.entries(artifact).reverse());
  const f = fixture([json(user), json(access), json([artifact]), head(), json([reordered])]);
  assert((await f.handler(request())).status === 200 && f.signs.length === 2);
});

Deno.test('error bodies are bounded and never echoed', async () => {
  const f = fixture([json('private-value'.repeat(10000))]);
  const response = await f.handler(request());
  assert(response.status >= 400 && !(await response.text()).includes('private-value'));
});

Deno.test('an already cancelled request cannot start service calls', async () => {
  const controller = new AbortController(); controller.abort();
  const req = new Request(request(), { signal: controller.signal });
  const f = fixture([]);
  assert((await f.handler(req)).status >= 400 && f.calls.length === 0);
});
