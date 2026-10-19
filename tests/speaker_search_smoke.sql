-- Run AFTER 005_speaker_search.sql in Supabase SQL Editor. All test data rolls back.
-- Uses its own @example.invalid rows and compares only those, so real data doesn't matter.
-- Every call passes (tags::jsonb, limit, speakers_only) so another ranked_speakers_search can't confuse it.
begin;
do $$
declare
  r record;
  names text[];
  got integer;
  failed boolean;
begin
  if exists (select 1 from public.alumni_intake_responses where email like 'speaker-smoke-%@example.invalid') then
    raise exception 'Reserved speaker-smoke test emails already exist';
  end if;

  insert into public.alumni_intake_responses
    (email, full_name, raw_answers, source_response_id, submitted_at, tagging_status, tags)
  values
    ('speaker-smoke-a@example.invalid', 'Smoke A',
     '{"speaking_types":["Speak as a panelist"],"audiences":["Students","Parents"],
       "availability":"Available soon - could start in 1-2 months","languages":"English and Spanish",
       "topic_familiarity":{"Leadership":"Deeply experienced - I work in this area professionally",
                            "Engineering":""}}',
     'smoke-a', now(), 'completed', '{"skills":["SmokeSkill"],"interests":["SmokeInterest"]}'),
    ('speaker-smoke-b@example.invalid', 'Smoke B',
     '{"speaking_types":["Virtual Events"],"audiences":["Students"],
       "availability":"Available now - ready to start within 2-4 weeks"}',
     'smoke-b', now(), 'completed', '{"skills":["SmokeSkill"],"interests":["SmokeInterest"]}'),
    ('speaker-smoke-c@example.invalid', 'Smoke C',
     '{"speaking_types":["Not Interested"],"audiences":["Students"]}',
     'smoke-c', now(), 'completed', '{"skills":["SmokeSkill"],"interests":["SmokeInterest"]}'),
    -- pending: its tags must not count (a resubmission clears tags anyway)
    ('speaker-smoke-d@example.invalid', 'Smoke D',
     '{"speaking_types":["Virtual Events"]}',
     'smoke-d', now(), 'pending', '{"skills":["SmokeSkill"]}');

  -- 1. Most matches first; equal matches -> available soonest first.
  select array_agg(full_name order by rank) into names
  from public.ranked_speakers_search('[{"category":"skills","tag":"SmokeSkill"},
                                {"category":"interests","tag":"SmokeInterest"},
                                {"category":"audiences","tag":"Parents"}]'::jsonb, 50, true)
  where full_name like 'Smoke %';
  if names is distinct from array['Smoke A', 'Smoke B'] then
    raise exception 'Ranking wrong: %', names;
  end if;
  select array_agg(full_name order by rank) into names
  from public.ranked_speakers_search('[{"category":"skills","tag":"SmokeSkill"}]'::jsonb, 50, true)
  where full_name like 'Smoke %';
  if names is distinct from array['Smoke B', 'Smoke A'] then
    raise exception 'Tie-break by availability wrong: %', names;
  end if;

  -- 2. "Not Interested" is not a speaker; p_speakers_only=false includes them. Pending tags never count.
  select count(*) into got from public.ranked_speakers_search('[{"category":"skills","tag":"SmokeSkill"}]'::jsonb, 50, false)
  where full_name like 'Smoke %';
  if got <> 3 then raise exception 'Expected 3 with p_speakers_only=false, got %', got; end if;

  -- 3. Missing tags, percent, grid, languages split, case-insensitive match.
  select * into r from public.ranked_speakers_search('[{"category":"deep_experience","tag":"Leadership"},
                                               {"category":"languages","tag":"spanish"},
                                               {"category":"skills","tag":"NoOneHasThis"}]'::jsonb, 50, true)
  where full_name = 'Smoke A';
  if r.match_count <> 2 or r.total_selected <> 3 or r.match_percent <> 67
     or r.missing_tags <> '[{"category":"skills","tag":"NoOneHasThis"}]'::jsonb then
    raise exception 'Match details wrong: % % % %', r.match_count, r.total_selected, r.match_percent, r.missing_tags;
  end if;
  if exists (select 1 from public.speaker_search_tags s join public.alumni_intake_responses x on x.id = s.response_id
             where x.email = 'speaker-smoke-a@example.invalid' and s.tag = 'Engineering') then
    raise exception 'A blank grid row became a tag';
  end if;

  -- 4. Limit, and nobody with zero matches.
  select count(*) into got from public.ranked_speakers_search('[{"category":"skills","tag":"SmokeSkill"}]'::jsonb, 1, true);
  if got <> 1 then raise exception 'Limit ignored'; end if;
  if exists (select 1 from public.ranked_speakers_search('[{"category":"skills","tag":"NoOneHasThis"}]'::jsonb, 50, true)) then
    raise exception 'Zero-match rows returned';
  end if;

  -- 5. Bad input is refused.
  foreach r.full_name in array array['[]', '[{"category":"skilz","tag":"x"}]', '[{"category":"skills","tag":""}]', '{}'] loop
    failed := false;
    begin
      perform public.ranked_speakers_search(r.full_name::jsonb, 5, true);
    exception when others then failed := true;
    end;
    if not failed then raise exception 'Bad input accepted: %', r.full_name; end if;
  end loop;

  raise notice 'Speaker search smoke tests passed; rolling test data back.';
end $$;
rollback;
