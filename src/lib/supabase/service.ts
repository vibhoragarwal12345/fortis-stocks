import { createClient } from "@supabase/supabase-js"

/**
 * Service-role Supabase client for server-only contexts (cron routes,
 * webhook handlers, anything that needs to read across users or bypass
 * RLS). NEVER expose this client to a browser bundle -- it carries the
 * service role key. Routes that import this MUST run on the Node runtime,
 * not Edge.
 */
export function createServiceClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY
  if (!url || !key) {
    throw new Error(
      "SUPABASE_SERVICE_ROLE_KEY / NEXT_PUBLIC_SUPABASE_URL missing — " +
        "service-role client unavailable"
    )
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  })
}
