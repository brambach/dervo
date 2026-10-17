import { createHandler } from './handler.ts';

// Server environment entries. DERVO_PADDLE_ENVIRONMENT must be 'sandbox' or 'live' and match the API key and
// dervo_private.billing_settings, or every request is refused. No service-role key is used.
const handler = createHandler({
  supabaseUrl: Deno.env.get('SUPABASE_URL') ?? '',
  publishableKey: Deno.env.get('DERVO_SUPABASE_PUBLISHABLE_KEY') ?? '',
  paddleApiKey: Deno.env.get('DERVO_PADDLE_API_KEY') ?? '',
  environment: Deno.env.get('DERVO_PADDLE_ENVIRONMENT') ?? '',
}, { fetch });
Deno.serve(handler);
