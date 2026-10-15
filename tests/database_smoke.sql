-- Run AFTER 001_tagging.sql in Supabase SQL Editor. All test data rolls back.
-- This test does not call OpenAI. No keys needed in the SQL text.
begin;
do $$
declare
  v_id uuid;
  job public.alumni_intake_responses;
  oldjob public.alumni_intake_responses;
  result jsonb := '{"tags":{"skills":["Python"]},"evidence":{},"model":"sql-test"}';
  saved boolean;
begin
  -- Use a dedicated test address and fail rather than overwrite an existing row.
  if exists(select 1 from public.alumni_intake_responses where email='sql-smoke-test@example.invalid') then
    raise exception 'Reserved SQL-test email already exists; choose another test address';
  end if;
  v_id := public.ingest_alumni_intake('sql-smoke-test@example.invalid','Test','{"skills":"Python"}', 'smoke-1', '2026-01-01');
  perform public.ingest_alumni_intake('sql-smoke-test@example.invalid','Test','{"skills":"Python"}', 'smoke-1', '2026-01-01');
  if (select revision from public.alumni_intake_responses where id=v_id) <> 1 then
    raise exception 'Exact retry was not idempotent';
  end if;
  -- Claim only our row via a lease update; tests completion race protections without touching other queued jobs.
  update public.alumni_intake_responses set tagging_status='processing', lease_token=gen_random_uuid(),
    lease_until=now()+interval '10 minutes', attempts=1 where id=v_id returning * into oldjob;
  perform public.ingest_alumni_intake('sql-smoke-test@example.invalid','Test','{"skills":"Java"}', 'smoke-2', '2026-01-02');
  saved := public.complete_alumni_tagging(v_id,oldjob.revision,oldjob.lease_token,result);
  if saved then raise exception 'Stale completion overwrote a newer response'; end if;
  if (select tags from public.alumni_intake_responses where id=v_id) is not null then
    raise exception 'Stale tags remain visible';
  end if;
  perform public.ingest_alumni_intake('sql-smoke-test@example.invalid','Test','{"skills":"Old"}', 'smoke-old', '2025-01-01');
  if (select raw_answers->>'skills' from public.alumni_intake_responses where id=v_id) <> 'Java' then
    raise exception 'Old response overwrote latest response';
  end if;
  update public.alumni_intake_responses set tagging_status='processing', lease_token=gen_random_uuid(),
    lease_until=now()+interval '10 minutes', attempts=1 where id=v_id returning * into job;
  saved := public.complete_alumni_tagging(v_id,job.revision,job.lease_token,result);
  if not saved then raise exception 'Current completion rejected'; end if;
  if public.complete_alumni_tagging(v_id,job.revision,job.lease_token,result) then
    raise exception 'Duplicate completion accepted';
  end if;
  if has_table_privilege('anon','public.alumni_intake_responses','SELECT') then
    raise exception 'Anonymous users can read intake';
  end if;
  if has_function_privilege('authenticated','public.claim_alumni_tagging()','EXECUTE') then
    raise exception 'Authenticated users can claim jobs';
  end if;
  raise notice 'Database smoke tests passed; rolling test data back.';
end $$;
rollback;
