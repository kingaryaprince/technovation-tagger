-- Speaker Bureau search (Week 7). Run once in Supabase SQL Editor, after 001_tagging.sql.
-- Safe to re-run: it creates or replaces one view, one small table (alumni_locations) and the
-- functions below. It does not change alumni_intake_responses or any existing data.
--
-- An admin picks tags; ranked_speakers_search returns the top 5 respondents ranked by how many
-- of the picked tags they match (most first). Ties: by name. Availability does not affect rank.
--
-- Tags come from two places on each alumni_intake_responses row:
--   * tags        (the LLM worker): industry, career_stage, project_domain,
--                 project_built_with, project_approach, interests, skills, story_angles
--   * raw_answers (form checkboxes/selects, never sent to the LLM): speaking_types,
--                 audiences, expertise_areas, story_formats, opportunities,
--                 interview_formats, interview_experience, regions, speaker_interest,
--                 availability, languages, and the familiarity grid as
--                 deep_experience / moderate_experience / emerging_interest
-- Category names match the keys in data/taxonomy.json and data/form_fields.json.
begin;

-- One row per (response, category, tag). The search and the filter list both read this.
create or replace view public.speaker_search_tags
with (security_invoker = true) as
-- LLM tags. Only completed rows: a resubmission clears tags and goes back to pending.
select r.id as response_id, c.key as category, btrim(t.value) as tag
from public.alumni_intake_responses r
cross join lateral jsonb_each(coalesce(r.tags, '{}'::jsonb)) as c
cross join lateral jsonb_array_elements_text(
  case when jsonb_typeof(c.value) = 'array' then c.value else '[]'::jsonb end) as t
where r.tagging_status = 'completed'

union all
-- Checkbox and multiple-choice answers, stored as the option text the alum picked.
select r.id, f.category, btrim(v.value)
from public.alumni_intake_responses r
cross join lateral (values
  ('speaking_types'), ('audiences'), ('expertise_areas'), ('story_formats'),
  ('opportunities'), ('interview_formats'), ('interview_experience'),
  ('regions'), ('speaker_interest')) as f(category)
cross join lateral jsonb_array_elements_text(
  case jsonb_typeof(r.raw_answers -> f.category)
    when 'array'  then r.raw_answers -> f.category
    when 'string' then jsonb_build_array(r.raw_answers -> f.category)
    else '[]'::jsonb
  end) as v
where btrim(v.value) <> ''
  and not (f.category in ('speaking_types', 'interview_formats')
           and btrim(v.value) ~* '^(not interested|none yet)$')

union all
-- Availability, shortened so admins pick "Available now" instead of the full option text.
select r.id, 'availability',
  case
    when r.raw_answers->>'availability' ~* '^\s*available now'  then 'Available now'
    when r.raw_answers->>'availability' ~* '^\s*available soon' then 'Available soon'
    when r.raw_answers->>'availability' ~* '^\s*interested but uncertain' then 'Uncertain timing'
    else btrim(r.raw_answers->>'availability')
  end
from public.alumni_intake_responses r
where jsonb_typeof(r.raw_answers -> 'availability') = 'string'
  and btrim(r.raw_answers->>'availability') <> ''

union all
-- Familiarity grid: {"AI & Machine Learning (research, ...)": "Deeply experienced - ..."}.
-- Works for a one-choice grid (string) or a checkbox grid (array). The row label's
-- trailing "(...)" is dropped, so the tag is just "AI & Machine Learning".
select r.id,
  case
    when lvl.value ~* '^\s*deeply'     then 'deep_experience'
    when lvl.value ~* '^\s*moderately' then 'moderate_experience'
    else 'emerging_interest'
  end,
  btrim(regexp_replace(g.key, '\s*\([^)]*\)\s*$', ''))
from public.alumni_intake_responses r
cross join lateral jsonb_each(
  case when jsonb_typeof(r.raw_answers -> 'topic_familiarity') = 'object'
       then r.raw_answers -> 'topic_familiarity' else '{}'::jsonb end) as g
cross join lateral jsonb_array_elements_text(
  case jsonb_typeof(g.value)
    when 'array'  then g.value
    when 'string' then jsonb_build_array(g.value)
    else '[]'::jsonb
  end) as lvl
where lvl.value ~* '^\s*(deeply|moderately|emerging)'

union all
-- Languages is a short-text answer ("English, Yoruba and Igbo"), so split it.
select r.id, 'languages', initcap(btrim(l.part))
from public.alumni_intake_responses r
cross join lateral regexp_split_to_table(
  case jsonb_typeof(r.raw_answers -> 'languages')
    when 'string' then r.raw_answers->>'languages'
    when 'array'  then (select string_agg(x, ',') from jsonb_array_elements_text(r.raw_answers -> 'languages') x)
    else ''
  end, '\s*(,|;|/|&|\mand\M)\s*') as l(part)
where btrim(l.part) <> '';

revoke all on public.speaker_search_tags from public, anon, authenticated;
grant select on public.speaker_search_tags to service_role;


-- Every category/tag that at least one respondent has, with a head count.
-- The admin UI uses this to build its filter checkboxes.
create or replace function public.speaker_filter_options()
returns table (category text, tag text, people integer)
language sql stable security invoker set search_path = '' as $$
  select s.category, s.tag, count(distinct s.response_id)::integer
  from public.speaker_search_tags s
  group by s.category, s.tag
  order by s.category, count(distinct s.response_id) desc, s.tag;
$$;


-- ---------------------------------------------------------------------------------------------
-- Travel radius. Where each speaker lives, as coordinates, so the search can measure distance.
-- Filled by `python3 -m alumni_tagger geocode-locations` from each person's current_location
-- answer ("City, State, Country"). lat/lng stay empty when the place could not be found.
create table if not exists public.alumni_locations (
  response_id   uuid primary key references public.alumni_intake_responses(id) on delete cascade,
  location_text text not null,
  lat           double precision,
  lng           double precision,
  geocoded_at   timestamptz not null default now()
);
alter table public.alumni_locations enable row level security;
revoke all on table public.alumni_locations from public, anon, authenticated;
grant select, insert, update, delete on table public.alumni_locations to service_role;

-- People whose current_location has not been looked up yet (or was edited since).
create or replace function public.locations_to_geocode()
returns table (response_id uuid, location_text text)
language sql stable security invoker set search_path = '' as $$
  select r.id, btrim(r.raw_answers->>'current_location')
  from public.alumni_intake_responses r
  left join public.alumni_locations l on l.response_id = r.id
  where jsonb_typeof(r.raw_answers -> 'current_location') = 'string'
    and btrim(r.raw_answers->>'current_location') <> ''
    and (l.response_id is null
         or l.location_text is distinct from btrim(r.raw_answers->>'current_location'));
$$;

-- Save one lookup result (lat/lng may be null = "place not found", so it is not retried forever).
create or replace function public.save_location(
  p_response_id uuid, p_location_text text, p_lat double precision, p_lng double precision)
returns table (saved boolean)
language sql volatile security invoker set search_path = '' as $$
  insert into public.alumni_locations (response_id, location_text, lat, lng)
  values (p_response_id, p_location_text, p_lat, p_lng)
  on conflict (response_id) do update
    set location_text = excluded.location_text, lat = excluded.lat, lng = excluded.lng,
        geocoded_at = now()
  returning true;
$$;


-- Named ranked_speakers_search so it doesn't clash with a teammate's draft search_speakers
-- (jsonb, boolean, integer) in the same project. Remove this file's earlier name, if it was run
-- under it: only the (jsonb, integer, boolean) signature, never the teammate's.
drop function if exists public.search_speakers(jsonb, integer, boolean);
-- The previous version of ranked_speakers_search (before the travel radius was added). The new
-- one has extra inputs and extra result columns, so the old one must go or calls would be ambiguous.
drop function if exists public.ranked_speakers_search(jsonb, integer, boolean);

-- p_tags: [{"category":"audiences","tag":"Students"}, {"category":"skills","tag":"Python"}]
--   (may be an empty list when a location is given)
-- p_limit: how many people to return (default 5, 1-50)
-- p_speakers_only: true = only people who picked a speaking type (or said yes to the
--   speaker question); false = everyone
-- p_lat, p_lng, p_radius_miles, p_place_label: optional travel radius. Being within the radius of
--   the point counts as ONE EXTRA MATCH (shown as "Within N mi of <place>"). Radius is in miles
--   (default 100). Distance is from the speaker's current_location.
-- p_include_virtual: true (default) = people outside the radius are still listed, just ranked lower.
--   false = only people within the radius are listed. Ignored when no location is given.
create or replace function public.ranked_speakers_search(
  p_tags jsonb, p_limit integer default 5, p_speakers_only boolean default true,
  p_lat double precision default null, p_lng double precision default null,
  p_radius_miles numeric default 100, p_place_label text default null,
  p_include_virtual boolean default true)
returns table (
  rank integer, response_id uuid, full_name text, email text,
  match_count integer, total_selected integer, match_percent integer,
  matched_tags jsonb, missing_tags jsonb, availability text, distance_miles integer,
  profile jsonb)
language plpgsql stable security invoker set search_path = '' as $$
#variable_conflict use_column
declare
  v_bad text;
  v_known text[] := array[
    'industry', 'career_stage', 'project_domain', 'project_built_with', 'project_approach',
    'interests', 'skills', 'story_angles', 'speaking_types', 'audiences', 'expertise_areas',
    'story_formats', 'opportunities', 'interview_formats', 'interview_experience', 'regions',
    'speaker_interest', 'availability', 'languages', 'deep_experience',
    'moderate_experience', 'emerging_interest'];
  v_has_loc boolean := p_lat is not null and p_lng is not null;
  v_radius numeric := coalesce(p_radius_miles, 100);
  v_place text := coalesce(nullif(btrim(p_place_label), ''), 'the chosen place');
  v_loc_tag text;
begin
  if p_tags is null or jsonb_typeof(p_tags) <> 'array' then
    raise exception 'p_tags must be a list of {"category": ..., "tag": ...}';
  end if;
  if jsonb_array_length(p_tags) = 0 and not v_has_loc then
    raise exception 'Pick at least one tag or a location: p_tags must be a list of {"category": ..., "tag": ...}';
  end if;
  if jsonb_array_length(p_tags) > 50 then
    raise exception 'Pick at most 50 tags';
  end if;
  if (p_lat is null) <> (p_lng is null) then
    raise exception 'Give both p_lat and p_lng, or neither';
  end if;
  if v_has_loc and (p_lat not between -90 and 90 or p_lng not between -180 and 180) then
    raise exception 'p_lat must be -90 to 90 and p_lng -180 to 180';
  end if;
  if v_radius < 1 or v_radius > 5000 then
    raise exception 'Use a radius of 1-5000 miles';
  end if;
  select string_agg(distinct coalesce(e->>'category', '(missing)'), ', ') into v_bad
  from jsonb_array_elements(p_tags) e
  where jsonb_typeof(e) <> 'object' or coalesce(btrim(e->>'tag'), '') = ''
     or not (coalesce(e->>'category', '') = any (v_known));
  if v_bad is not null then
    raise exception 'Unknown category or empty tag: %. Categories: %', v_bad, array_to_string(v_known, ', ');
  end if;
  v_loc_tag := 'Within ' || round(v_radius)::integer::text || ' mi of ' || v_place;

  return query
  with picked as (
    select distinct e->>'category' as category, btrim(e->>'tag') as tag
    from jsonb_array_elements(p_tags) e
  ),
  -- The radius counts as one more thing to match, when a location was given.
  total as (select ((select count(*) from picked) + case when v_has_loc then 1 else 0 end)::integer as n),
  hits as (
    -- case-insensitive, so "students" still matches "Students"
    select distinct s.response_id, p.category, p.tag
    from picked p
    join public.speaker_search_tags s
      on s.category = p.category and lower(s.tag) = lower(p.tag)
  ),
  tag_scores as (
    select h.response_id, count(*)::integer as hit_count,
      jsonb_agg(jsonb_build_object('category', h.category, 'tag', h.tag)
                order by h.category, h.tag) as hit_tags
    from hits h group by h.response_id
  ),
  dist as (
    -- Great-circle distance in miles between the picked point and the speaker's location.
    select l.response_id,
      3958.8 * acos(least(1.0, greatest(-1.0,
        sin(radians(p_lat)) * sin(radians(l.lat)) +
        cos(radians(p_lat)) * cos(radians(l.lat)) * cos(radians(l.lng - p_lng))))) as miles
    from public.alumni_locations l
    where v_has_loc and l.lat is not null and l.lng is not null
  ),
  scored as (
    select r.id as response_id,
      coalesce(ts.hit_count, 0) + (case when coalesce(d.miles <= v_radius, false) then 1 else 0 end) as match_count,
      coalesce(ts.hit_tags, '[]'::jsonb)
        || (case when coalesce(d.miles <= v_radius, false)
                 then jsonb_build_array(jsonb_build_object('category', 'location', 'tag', v_loc_tag))
                 else '[]'::jsonb end) as matched_tags,
      coalesce(d.miles <= v_radius, false) as in_radius,
      d.miles as miles
    from public.alumni_intake_responses r
    left join tag_scores ts on ts.response_id = r.id
    left join dist d on d.response_id = r.id
    where coalesce(ts.hit_count, 0) > 0 or coalesce(d.miles <= v_radius, false)
  ),
  people as (
    select sc.*, r.full_name, r.email, r.tags, r.raw_answers,
      (select a.tag from public.speaker_search_tags a
        where a.response_id = r.id and a.category = 'availability' limit 1) as availability
    from scored sc
    join public.alumni_intake_responses r on r.id = sc.response_id
    where (not p_speakers_only
           or exists (select 1 from public.speaker_search_tags sp
                      where sp.response_id = r.id and sp.category = 'speaking_types')
           or r.raw_answers->>'speaker_interest' ~* '^\s*yes')
      -- "Include virtual speakers" off: only people inside the radius.
      and (p_include_virtual or not v_has_loc or sc.in_radius)
  ),
  ranked as (
    -- Most matches first. Ties: closer first (only when a location was given), then name.
    select p.*,
      row_number() over (order by p.match_count desc, p.miles asc nulls last,
        lower(p.full_name), p.response_id)::integer as rank
    from people p
  )
  select k.rank, k.response_id, k.full_name, k.email, k.match_count, t.n,
    round(100.0 * k.match_count / t.n)::integer,
    k.matched_tags,
    coalesce((select jsonb_agg(jsonb_build_object('category', p.category, 'tag', p.tag)
                               order by p.category, p.tag)
              from picked p
              where not exists (select 1 from hits h where h.response_id = k.response_id
                                and h.category = p.category and h.tag = p.tag)), '[]'::jsonb)
      || (case when v_has_loc and not k.in_radius
               then jsonb_build_array(jsonb_build_object('category', 'location', 'tag', v_loc_tag))
               else '[]'::jsonb end),
    k.availability,
    round(k.miles)::integer,
    -- What an admin needs to decide; birthdate and the raw form dump are left out.
    jsonb_build_object(
      'current_role', k.raw_answers->'current_role',
      'current_location', k.raw_answers->'current_location',
      'timezone', k.raw_answers->'timezone',
      'languages', k.raw_answers->'languages',
      'participation_years', k.raw_answers->'participation_years',
      'project', k.raw_answers->'project',
      'since_technovation', k.raw_answers->'since_technovation',
      'speaking_types', k.raw_answers->'speaking_types',
      'audiences', k.raw_answers->'audiences',
      'regions', k.raw_answers->'regions',
      'tags', coalesce(k.tags, '{}'::jsonb))
  from ranked k cross join total t
  where k.rank <= greatest(1, least(coalesce(p_limit, 5), 50))
  order by k.rank;
end $$;

revoke all on function public.speaker_filter_options() from public, anon, authenticated;
revoke all on function public.locations_to_geocode() from public, anon, authenticated;
revoke all on function public.save_location(uuid, text, double precision, double precision) from public, anon, authenticated;
revoke all on function public.ranked_speakers_search(jsonb, integer, boolean, double precision, double precision, numeric, text, boolean) from public, anon, authenticated;
grant execute on function public.speaker_filter_options() to service_role;
grant execute on function public.locations_to_geocode() to service_role;
grant execute on function public.save_location(uuid, text, double precision, double precision) to service_role;
grant execute on function public.ranked_speakers_search(jsonb, integer, boolean, double precision, double precision, numeric, text, boolean) to service_role;
notify pgrst, 'reload schema';
commit;
