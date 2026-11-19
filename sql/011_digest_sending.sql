-- 011: what the digest sender needs. Run once in the Supabase SQL Editor. Safe to run again.
--
-- digest_deliveries: one row per digest actually emailed to an alum, so the sender never emails
--   the same person twice too soon or repeats an opportunity they already got.
-- digest_unsubscribes: emails that asked to stop. The sender skips them every run.
--
-- (The older digest_sends table points at alumni_profiles, which the intake form does not fill,
--  so it is left as it is.)

create table if not exists public.digest_deliveries (
  id bigint generated always as identity primary key,
  response_id uuid not null references public.alumni_intake_responses(id) on delete cascade,
  email text not null,
  opportunity_keys text[] not null default '{}',
  message_id text,
  sent_at timestamptz not null default now()
);
create index if not exists digest_deliveries_response_sent
  on public.digest_deliveries (response_id, sent_at desc);

create table if not exists public.digest_unsubscribes (
  email text primary key check (email = lower(email)),
  source text not null default 'email reply',
  created_at timestamptz not null default now()
);

-- Only the secret (service) key reads or writes these; the public key sees nothing.
alter table public.digest_deliveries enable row level security;
alter table public.digest_unsubscribes enable row level security;
revoke all on public.digest_deliveries, public.digest_unsubscribes from anon, authenticated;

-- Check: both tables should be listed.
select table_name from information_schema.tables
where table_schema = 'public' and table_name in ('digest_deliveries', 'digest_unsubscribes');
