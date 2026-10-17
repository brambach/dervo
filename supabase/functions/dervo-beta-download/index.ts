import { AwsClient } from 'npm:aws4fetch@1.0.20';
import { createHandler } from './handler.ts';

// These names are server environment entries. No service-role key is used.
const config = {
  supabaseUrl: Deno.env.get('SUPABASE_URL') ?? '',
  publishableKey: Deno.env.get('DERVO_SUPABASE_PUBLISHABLE_KEY') ?? '',
  r2Account: Deno.env.get('DERVO_R2_ACCOUNT_ID') ?? '',
};
const accessKeyId = Deno.env.get('DERVO_R2_ACCESS_KEY_ID') ?? '';
const secretAccessKey = Deno.env.get('DERVO_R2_SECRET_ACCESS_KEY') ?? '';
let signer: AwsClient | undefined;
const handler = createHandler(config, { fetch, sign: async (request, query) => {
  if (!/^[a-f0-9]{32}$/.test(accessKeyId) || !/^[a-f0-9]{64}$/.test(secretAccessKey)) {
    throw new Error('Server download setup is incomplete');
  }
  signer ??= new AwsClient({ accessKeyId, secretAccessKey, service: 's3', region: 'auto' });
  return signer.sign(request, { aws: { signQuery: query } });
} });
Deno.serve(handler);
