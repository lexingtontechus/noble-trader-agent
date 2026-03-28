import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON) {
  throw new Error(
    "Missing Supabase env vars — add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to .env.local"
  );
}

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON);

// Authenticated client factory — forwards Clerk JWT for RLS
export function getAuthClient(clerkToken) {
  return createClient(SUPABASE_URL, SUPABASE_ANON, {
    global: {
      headers: { Authorization: `Bearer ${clerkToken}` },
    },
  });
}
