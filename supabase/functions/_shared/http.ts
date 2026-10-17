// Small request helpers shared by the billing functions. Never log request headers, bodies, secrets or URLs.
export class Failure extends Error {
  constructor(readonly status: number) { super('Request failed'); }
}

export function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
  } });
}

/** The body as UTF-8 text, refusing anything over ``max`` bytes or not valid UTF-8. */
export async function textBody(input: Request | Response, max: number, signal: AbortSignal): Promise<string> {
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
      if (bytes.length + value.length > max) throw new Failure(413);
      const next = new Uint8Array(bytes.length + value.length);
      next.set(bytes); next.set(value, bytes.length); bytes = next;
    }
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch (error) {
    await reader.cancel().catch(() => {});
    if (error instanceof Failure) throw error;
    throw new Failure(400);
  } finally { signal.removeEventListener('abort', cancel); reader.releaseLock(); }
}

export async function jsonBody(input: Request | Response, max: number, signal: AbortSignal): Promise<unknown> {
  const text = await textBody(input, max, signal);
  try { return JSON.parse(text); } catch { throw new Failure(400); }
}

export function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export const supabaseUrlPattern = /^https:\/\/[a-z0-9]{20}\.supabase\.co$/;
export const publishableKeyPattern = /^sb_publishable_[A-Za-z0-9_-]{10,256}$/;
