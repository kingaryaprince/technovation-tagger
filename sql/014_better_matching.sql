-- Matching v2: replaces top_opportunities_for_alum from sql/012_top_opportunities.sql.
-- Run once in the Supabase SQL editor; safe to re-run. digest.py's rank() is the Python copy;
-- keep the two in step (tests/test_matching_sql.cjs checks they agree).
--
-- What changed from 012:
--   * Internships go only to alumni who are students: career_stage Student or
--     Student + Working, or no career_stage yet. Experienced alumni, founders and early-career
--     alumni (who mostly can't take an internship) no longer get them.
--   * Generic tags count less. Soft-skill and career labels that fit almost any listing
--     (Teamwork/Collaboration, Leadership, Writing/Content Creation...) score 1; every other
--     shared tag (Python, SQL, Healthcare, Volunteering...) scores 2. Before, a listing that
--     shared three soft skills outranked one that shared Python and Data Analysis.
--   * The minimum score is 2: at least one specific tag, or two generic ones. A listing that
--     only shares "Job Searching" or "Teamwork/Collaboration" is no longer a match.
--   * matched_tags lists specific tags first, so "Why we picked it" leads with the real reason.
-- Ties still take turns between sources, and the result columns are unchanged.

CREATE OR REPLACE FUNCTION public.top_opportunities_for_alum(
  p_alum_id uuid, p_limit integer DEFAULT 5, p_min_score integer DEFAULT 2)
 RETURNS TABLE(opportunity_id uuid, title text, type text, organization text, url text,
               deadline date, rolling boolean, score integer, matched_tags text[])
 LANGUAGE sql
 STABLE
AS $function$
  with settings as (
    select
      -- Same list as GENERIC_TAGS in alumni_tagger/digest.py.
      array['Career Development', 'Cross-Cultural Communication', 'Facilitation',
            'Job Searching', 'Leadership', 'Mentorship', 'Networking', 'Organization',
            'Problem Solving', 'Public Speaking', 'Teamwork/Collaboration',
            'Technical Communication', 'Time Management',
            'Writing/Content Creation']::text[] as generic,
      -- Same list as INTERNSHIP_STAGES in alumni_tagger/digest.py.
      array['Student', 'Student + Working']::text[] as internship_stages
  ),
  alum as (
    select
      -- The alum's tags from interests, skills and industry (the categories opportunities use).
      array(select distinct t
            from jsonb_each(case when jsonb_typeof(a.tags) = 'object' then a.tags end) e
            cross join lateral jsonb_array_elements_text(
              case when jsonb_typeof(e.value) = 'array' then e.value end) t
            where e.key in ('interests', 'skills', 'industry')) as tags,
      array(select jsonb_array_elements_text(
              case when jsonb_typeof(a.tags -> 'career_stage') = 'array'
                   then a.tags -> 'career_stage' end)) as stages
    from alumni_intake_responses a
    where a.id = p_alum_id
  ),
  scored as (
    select o.*, m.matched, m.score
    from opportunities o
    cross join alum al
    cross join settings st
    cross join lateral (
      select coalesce(array_agg(t order by (t = any(st.generic)), t), '{}') as matched,
             coalesce(sum(case when t = any(st.generic) then 1 else 2 end), 0)::integer as score
      from (select unnest(o.tags) intersect select unnest(al.tags)) shared(t)
    ) m
    where coalesce(o.status, '') <> 'expired'
      and (coalesce(o.rolling, false)                -- rolling
           or o.deadline is null                      -- no deadline listed: still open
           or o.deadline >= current_date)             -- upcoming
      and (o.type is distinct from 'internship'       -- internships: students only
           or cardinality(al.stages) = 0
           or al.stages && st.internship_stages)
      and m.score >= p_min_score
  ),
  ranked as (
    -- Within the same score, take turns between sources (1st from each, then 2nd from each...).
    select s.*, row_number() over (
             partition by s.score, s.source
             order by s.deadline asc nulls last, s.created_at desc) as turn
    from scored s
  )
  select r.id, r.title, r.type, r.organization, r.url, r.deadline, r.rolling,
         r.score, r.matched
  from ranked r
  order by r.score desc, r.turn asc, r.deadline asc nulls last, r.created_at desc
  limit p_limit;
$function$;
