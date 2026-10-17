// Server-only download authorization. Never log request headers, secrets or URLs.
export type Config = { supabaseUrl: string; publishableKey: string; r2Account: string };
export type Signer = (request: Request, query: boolean) => Promise<Request>;
type Dependencies = { fetch: typeof fetch; sign: Signer; now?: () => number };
const bucket = 'dervo-beta-releases';
const seconds = 120;
const idPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const hashPattern = /^[a-f0-9]{64}$/;
const columns = 'release_id,platform,object_name,download_origin,sha256,bytes,format';
type Artifact = { release_id: string; platform: string; object_name: string; download_origin: string;
  sha256: string; bytes: number; format: string };
// Each platform's installer formats (supabase/migrations/202609260001_dervo_windows_releases.sql and
// 202610080001_dervo_linux_releases.sql). A Windows row's release id ends in -windows and a Mac row's never does; a
// Linux row's ends in -linux- and its format, and no other row's does.
const formats: Record<string, string[]> = { 'macos-arm64': ['zip', 'dmg'], 'windows-x64': ['exe'],
  'linux-x64': ['deb', 'rpm', 'appimage'] };
const linuxId = /-linux-(deb|rpm|appimage)$/;

class Failure extends Error {
  constructor(readonly status: number) { super('Download unavailable'); }
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
  } });
}

async function jsonBody(input: Request | Response, max: number, signal: AbortSignal): Promise<unknown> {
  if (!input.body) throw new Failure(400);
  const reader = input.body.getReader();
  const cancel = () => { void reader.cancel().catch(() => {}); };
  let bytes = new Uint8Array();
  try {
    signal.throwIfAborted();
    signal.addEventListener('abort', cancel, { once: true });
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      if (bytes.length + value.length > max) throw new Failure(400);
      const next = new Uint8Array(bytes.length + value.length);
      next.set(bytes); next.set(value, bytes.length); bytes = next;
    }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch (error) {
    await reader.cancel().catch(() => {});
    if (error instanceof Failure) throw error;
    throw new Failure(400);
  } finally { signal.removeEventListener('abort', cancel); reader.releaseLock(); }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function artifact(value: unknown, releaseId: string, origin: string, hash: string): Artifact {
  if (!Array.isArray(value) || value.length !== 1 || !object(value[0])) throw new Failure(403);
  const row = value[0];
  if (Object.keys(row).sort().join() !== columns.split(',').sort().join()
      || row.release_id !== releaseId || row.download_origin !== origin || row.sha256 !== hash
      || !Object.hasOwn(formats, String(row.platform)) || !formats[String(row.platform)].includes(String(row.format))
      || releaseId.endsWith('-windows') !== (row.platform === 'windows-x64')
      || linuxId.test(releaseId) !== (row.platform === 'linux-x64')
      || (row.platform === 'linux-x64' && !releaseId.endsWith('-linux-' + row.format))
      || row.object_name !== `${releaseId}/Dervo-${row.platform}.${row.format}`
      || !Number.isSafeInteger(row.bytes) || Number(row.bytes) <= 0 || Number(row.bytes) > 2147483648) {
    throw new Failure(403);
  }
  return row as Artifact;
}

export function createHandler(config: Config, deps: Dependencies): (request: Request) => Promise<Response> {
  const validConfig = /^https:\/\/[a-z0-9]{20}\.supabase\.co$/.test(config.supabaseUrl)
    && /^sb_publishable_[A-Za-z0-9_-]{10,256}$/.test(config.publishableKey)
    && /^[a-f0-9]{32}$/.test(config.r2Account);
  const origin = `https://${config.r2Account}.r2.cloudflarestorage.com`;
  const now = deps.now ?? Date.now;
  return async (request) => {
    try {
      if (!validConfig) throw new Failure(503);
      if (request.method !== 'POST') throw new Failure(405);
      const authorization = request.headers.get('Authorization') ?? '';
      if (!/^Bearer [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(authorization)
          || authorization.length > 32775) throw new Failure(401);
      if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw new Failure(400);
      const signal = AbortSignal.any([request.signal, AbortSignal.timeout(15000)]);
      const body = await jsonBody(request, 1024, signal);
      if (!object(body) || Object.keys(body).sort().join() !== 'releaseId,sha256'
          || typeof body.releaseId !== 'string' || !idPattern.test(body.releaseId)
          || typeof body.sha256 !== 'string' || !hashPattern.test(body.sha256)) throw new Failure(400);
      const releaseId = body.releaseId, hash = body.sha256;
      const headers = { Authorization: authorization, apikey: config.publishableKey, Accept: 'application/json',
        'Content-Type': 'application/json', 'Cache-Control': 'no-cache' };
      async function api(path: string, method = 'GET', body?: string): Promise<unknown> {
        const response = await deps.fetch(config.supabaseUrl + path,
          { method, headers, body, signal, redirect: 'error', cache: 'no-store' });
        if (!response.ok) {
          await response.body?.cancel();
          throw new Failure(response.status === 401 || response.status === 403 ? 403 : 503);
        }
        return jsonBody(response, 65536, signal);
      }
      // Verify the user's token with Auth. Never trust decoded JWT claims alone.
      const user = await api('/auth/v1/user');
      if (!object(user) || typeof user.id !== 'string'
          || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(user.id)
          || user.is_anonymous !== false || typeof user.email_confirmed_at !== 'string'
          || !Number.isFinite(Date.parse(user.email_confirmed_at))) throw new Failure(403);
      const access = await api('/rest/v1/rpc/dervo_access', 'POST', '{}');
      if (!object(access) || access.allowed !== true || access.status !== 'approved'
          || access.release_channel !== 'private-beta'
          || (access.expires_at !== null && (typeof access.expires_at !== 'string'
            || !Number.isFinite(Date.parse(access.expires_at)) || Date.parse(access.expires_at) <= now()))) {
        throw new Failure(403);
      }
      const query = '/rest/v1/dervo_beta_releases?select=' + columns + '&release_id=eq.' + releaseId + '&limit=1';
      const release = artifact(await api(query), releaseId, origin, hash);
      const objectUrl = origin + '/' + bucket + '/' + release.object_name;
      // HEAD uses a bucket-scoped read-only credential, never the user's JWT.
      const headRequest = await deps.sign(new Request(objectUrl, { method: 'HEAD' }), false);
      const head = await deps.fetch(headRequest, { signal, redirect: 'error', cache: 'no-store' });
      if (!head.ok || head.headers.get('content-length') !== String(release.bytes)
          || head.headers.get('x-amz-meta-sha256') !== hash) {
        await head.body?.cancel(); throw new Failure(503);
      }
      await head.body?.cancel();
      // Re-read through RLS after R2 responds. Withdrawal, revocation or an
      // edited release can't turn a stale lookup into a fresh signed link.
      const final = artifact(await api(query), releaseId, origin, hash);
      if ((Object.keys(release) as (keyof Artifact)[]).some((key) => final[key] !== release[key])) {
        throw new Failure(409);
      }
      signal.throwIfAborted();
      const issued = Math.floor(now() / 1000) * 1000;
      const url = new URL(objectUrl);
      url.searchParams.set('X-Amz-Expires', String(seconds));
      url.searchParams.set('response-content-disposition', `attachment; filename="Dervo-${releaseId}.${release.format}"`);
      const signed = await deps.sign(new Request(url), true);
      signal.throwIfAborted();
      if (now() >= issued + seconds * 1000) throw new Failure(503);
      return reply(200, { releaseId, sha256: hash, bytes: release.bytes, url: signed.url,
        expiresAt: Math.floor(issued / 1000) + seconds });
    } catch (error) {
      return reply(error instanceof Failure ? error.status : 503,
        { error: 'The beta download couldn’t be prepared. Check your Dervo access and try again.' });
    }
  };
}
