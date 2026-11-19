-- Matching: the top opportunities for one alum.
-- Score = how many of the alum's interests/skills/industry tags the opportunity shares.
--
-- Changes from the first version (which only lived in Supabase):
--   * Opportunities with no deadline (e.g. Simplify internships) now count as open,
--     unless they are marked expired. Before, they were silently left out.
--   * Ties are spread across sources, so one board (e.g. AmeriCorps) can't fill
--     the whole digest when other boards have equally good matches.

CREATE OR REPLACE FUNCTION public.top_opportunities_for_alum(
  p_alum_id uuid, p_limit integer DEFAULT 5, p_min_score integer DEFAULT 1)
 RETURNS TABLE(opportunity_id uuid, title text, type text, organization text, url text,
               deadline date, rolling boolean, score integer, matched_tags text[])
 LANGUAGE sql
 STABLE
AS $function$
  with alum_tags as (
    -- The alum's tags from interests, skills and industry (the categories opportunities use).
    select array_agg(distinct t) as tags
    from alumni_intake_responses a
    cross join lateral jsonb_each(case when jsonb_typeof(a.tags) = 'object' then a.tags end) e
    cross join lateral jsonb_array_elements_text(
      case when jsonb_typeof(e.value) = 'array' then e.value end) t
    where a.id = p_alum_id
      and e.key in ('interests', 'skills', 'industry')
  ),
  scored as (
    select o.*, m.matched, cardinality(m.matched) as n
    from opportunities o
    cross join alum_tags at
    cross join lateral (
      select array(select unnest(o.tags) intersect select unnest(at.tags) order by 1) as matched
    ) m
    where coalesce(o.status, '') <> 'expired'
      and (coalesce(o.rolling, false)                -- rolling
           or o.deadline is null                      -- no deadline listed: still open
           or o.deadline >= current_date)             -- upcoming
      and cardinality(m.matched) >= p_min_score
  ),
  ranked as (
    -- Within the same score, take turns between sources (1st from each, then 2nd from each...).
    select s.*, row_number() over (
             partition by s.n, s.source
             order by s.deadline asc nulls last, s.created_at desc) as turn
    from scored s
  )
  select r.id, r.title, r.type, r.organization, r.url, r.deadline, r.rolling,
         r.n, r.matched
  from ranked r
  order by r.n desc, r.turn asc, r.deadline asc nulls last, r.created_at desc
  limit p_limit;
$function$;
