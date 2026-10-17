// Paddle webhook deliveries. This function holds no secret and decides nothing: it passes the raw body and the
// Paddle-Signature header, exactly as received, to public.dervo_billing_ingest(), which checks the signature against
// the Vault secret and applies the event (supabase/migrations/202610080001_dervo_billing.sql). It uses only the
// publishable key. Paddle retries anything that isn't a 2xx, which is what a rejected or failed delivery should get.
import { Failure, object, publishableKeyPattern, reply, supabaseUrlPattern, textBody } from '../_shared/http.ts';

export type Config = { supabaseUrl: string; publishableKey: string };
type Dependencies = { fetch: typeof fetch };
const maxBody = 262144;

export function createHandler(config: Config, deps: Dependencies): (request: Request) => Promise<Response> {
  const validConfig = supabaseUrlPattern.test(config.supabaseUrl) && publishableKeyPattern.test(config.publishableKey);
  return async (request) => {
    try {
      if (!validConfig) throw new Failure(503);
      if (request.method !== 'POST') throw new Failure(405);
      const signature = request.headers.get('Paddle-Signature') ?? '';
      if (!signature || signature.length > 1024 || !/^[A-Za-z0-9=;]+$/.test(signature)) throw new Failure(401);
      const signal = AbortSignal.any([request.signal, AbortSignal.timeout(15000)]);
      const payload = await textBody(request, maxBody, signal);
      const response = await deps.fetch(config.supabaseUrl + '/rest/v1/rpc/dervo_billing_ingest', {
        method: 'POST', signal, redirect: 'error', cache: 'no-store',
        headers: { apikey: config.publishableKey, Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, signature }),
      });
      if (!response.ok) {
        await response.body?.cancel();
        // A signature the database refuses is 401/403 from PostgREST; a malformed event is 400.
        throw new Failure(response.status === 401 || response.status === 403 ? 401 : response.status === 400 ? 400 : 503);
      }
      const result = JSON.parse(await textBody(response, 4096, signal));
      if (!object(result) || typeof result.state !== 'string') throw new Failure(503);
      return reply(200, { state: result.state });
    } catch (error) {
      return reply(error instanceof Failure ? error.status : 503, { error: 'Delivery not accepted' });
    }
  };
}
