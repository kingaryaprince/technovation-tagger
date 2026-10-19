-- Run once in the Supabase SQL Editor. Additive week-three prototype.
-- Does not modify public.alumni_profiles or its policies.
-- Raw answers and derived tags are restricted to server-side service_role access.
begin;

create table public.alumni_intake_responses (
  id uuid primary key default gen_random_uuid(),
  email text not null unique check (email = lower(btrim(email)) and email <> ''),
  full_name text not null,
  raw_answers jsonb not null check (jsonb_typeof(raw_answers) = 'object'),
  source text not null default 'google_form',
  source_response_id text not null,
  submitted_at timestamptz not null,
  revision integer not null default 1,
  tagging_status text not null default 'pending'
    check (tagging_status in ('pending','processing','completed','failed')),
  attempts integer not null default 0,
  lease_token uuid,
  lease_until timestamptz,
  tags jsonb,
  evidence jsonb,
  model text,
  taxonomy_version text,
  prompt_version text,
  fingerprint text,
  usage jsonb,
  last_error text,
  tagged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index alumni_intake_tags_gin on public.alumni_intake_responses using gin(tags jsonb_path_ops);
create index alumni_intake_pending on public.alumni_intake_responses(tagging_status, updated_at);
alter table public.alumni_intake_responses enable row level security;
revoke all on public.alumni_intake_responses from public, anon, authenticated;
grant select, insert, update, delete on public.alumni_intake_responses to service_role;

-- A single transaction handles repeat submissions and concurrent arrivals.
-- Older timestamps cannot overwrite a more recent response. An exact retry is a no-op.
create function public.ingest_alumni_intake(
  p_email text, p_full_name text, p_answers jsonb,
  p_response_id text, p_submitted_at timestamptz
) returns uuid language plpgsql security invoker set search_path = '' as $$
declare v_id uuid;
begin
  if p_email is null or p_email !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
     or coalesce(btrim(p_full_name), '') = '' or coalesce(p_response_id, '') = ''
     or p_submitted_at is null or p_answers is null or jsonb_typeof(p_answers) <> 'object' then
    raise exception 'Invalid intake payload';
  end if;
  insert into public.alumni_intake_responses as old
    (email, full_name, raw_answers, source_response_id, submitted_at)
    values (lower(btrim(p_email)), btrim(p_full_name), p_answers, p_response_id, p_submitted_at)
  on conflict(email) do update set
    full_name = excluded.full_name, raw_answers = excluded.raw_answers,
    source_response_id = excluded.source_response_id, submitted_at = excluded.submitted_at,
    revision = old.revision + 1, tagging_status = 'pending', attempts = 0,
    lease_token = null, lease_until = null, tags = null, evidence = null,
    model = null, taxonomy_version = null, prompt_version = null, fingerprint = null,
    usage = null, last_error = null, tagged_at = null, updated_at = now()
  where excluded.submitted_at >= old.submitted_at and
    (excluded.raw_answers is distinct from old.raw_answers
     or excluded.full_name is distinct from old.full_name
     or excluded.source_response_id is distinct from old.source_response_id)
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.alumni_intake_responses where email = lower(btrim(p_email));
  end if;
  return v_id;
end $$;

create function public.claim_alumni_tagging()
returns setof public.alumni_intake_responses
language plpgsql security invoker set search_path = '' as $$
begin
  -- A worker interrupted three times does not remain indefinitely in processing.
  update public.alumni_intake_responses
    set tagging_status = 'failed', last_error = 'LeaseExpired', lease_token = null, lease_until = null
    where tagging_status = 'processing' and lease_until < now() and attempts >= 3;
  return query
  with candidate as (
    select id from public.alumni_intake_responses
    where (tagging_status = 'pending' or (tagging_status = 'processing' and lease_until < now()))
      and attempts < 3
    order by updated_at, id
    for update skip locked limit 1
  )
  update public.alumni_intake_responses as job
    set tagging_status = 'processing', attempts = job.attempts + 1,
        lease_token = gen_random_uuid(), lease_until = now() + interval '10 minutes', updated_at = now()
    from candidate where job.id = candidate.id returning job.*;
end $$;

create function public.complete_alumni_tagging(p_id uuid, p_revision integer, p_token uuid, p_result jsonb)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare v_count integer;
begin
  if p_result is null or jsonb_typeof(p_result->'tags') is distinct from 'object'
     or jsonb_typeof(p_result->'evidence') is distinct from 'object' then
    raise exception 'Invalid tagging result';
  end if;
  update public.alumni_intake_responses set
    tags = p_result->'tags', evidence = p_result->'evidence', model = p_result->>'model',
    taxonomy_version = p_result->>'taxonomy_version', prompt_version = p_result->>'prompt_version',
    fingerprint = p_result->>'fingerprint', usage = p_result->'usage',
    tagging_status = 'completed', tagged_at = now(), updated_at = now(),
    lease_token = null, lease_until = null, last_error = null
  where id = p_id and revision = p_revision and lease_token = p_token
    and tagging_status = 'processing' and lease_until > now();
  get diagnostics v_count = row_count;
  return v_count = 1;
end $$;

create function public.fail_alumni_tagging(p_id uuid, p_revision integer, p_token uuid, p_error text)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare v_count integer;
begin
  update public.alumni_intake_responses set
    tagging_status = 'failed', last_error = left(p_error, 100), lease_token = null,
    lease_until = null, updated_at = now()
  where id = p_id and revision = p_revision and lease_token = p_token and tagging_status = 'processing';
  get diagnostics v_count = row_count;
  return v_count = 1;
end $$;

revoke all on function public.ingest_alumni_intake(text,text,jsonb,text,timestamptz) from public, anon, authenticated;
revoke all on function public.claim_alumni_tagging() from public, anon, authenticated;
revoke all on function public.complete_alumni_tagging(uuid,integer,uuid,jsonb) from public, anon, authenticated;
revoke all on function public.fail_alumni_tagging(uuid,integer,uuid,text) from public, anon, authenticated;
grant execute on function public.ingest_alumni_intake(text,text,jsonb,text,timestamptz) to service_role;
grant execute on function public.claim_alumni_tagging() to service_role;
grant execute on function public.complete_alumni_tagging(uuid,integer,uuid,jsonb) to service_role;
grant execute on function public.fail_alumni_tagging(uuid,integer,uuid,text) to service_role;
notify pgrst, 'reload schema';
commit;
