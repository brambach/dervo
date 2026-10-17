import { createHandler } from './handler.ts';

// Server environment entries. No service-role key and no Paddle secret: the signature is checked in the database.
const handler = createHandler({
  supabaseUrl: Deno.env.get('SUPABASE_URL') ?? '',
  publishableKey: Deno.env.get('DERVO_SUPABASE_PUBLISHABLE_KEY') ?? '',
}, { fetch });
Deno.serve(handler);
