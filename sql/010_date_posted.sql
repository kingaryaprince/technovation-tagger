-- Add opportunities.date_posted (the day the job list says the listing was posted) and
-- fill it in for rows already imported. Run once in the Supabase SQL editor; safe to re-run.
--
-- New imports set date_posted themselves (alumni_tagger/job_import.py). Rows imported
-- before this column existed are backfilled from the "Posted: YYYY-MM-DD." line that the
-- importer writes into the description of every listings.json listing (SimplifyJobs and
-- lists that copy its format). README-table lists have no reliable dates and stay null.

alter table public.opportunities add column if not exists date_posted date;

update public.opportunities
set date_posted = substring(description from 'Posted: (\d{4}-\d{2}-\d{2})\.')::date
where date_posted is null
  and source is not null
  and description ~ 'Posted: \d{4}-\d{2}-\d{2}\.';

-- Check: how many rows per source now have a date.
select source,
       count(*) as total,
       count(date_posted) as with_date,
       min(date_posted) as oldest,
       max(date_posted) as newest
from public.opportunities
where source is not null
group by source
order by source;
