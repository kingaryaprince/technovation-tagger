-- Add opportunities.posting_text: the full text of the employer's job posting, fetched by
-- alumni_tagger/job_import.py from Workday, Greenhouse, Ashby, Lever and SmartRecruiters
-- links (alumni_tagger/postings.py). The opportunity tagger quotes from it, so job-list
-- imports get skill tags instead of only "Job Searching" and an industry.
-- Run once in the Supabase SQL editor; safe to re-run.
--
--   null  not fetched yet (or the link is on another site)
--   ''    fetched, but the site says the job is gone or the posting has no text

alter table public.opportunities add column if not exists posting_text text;

-- Check: the column exists (expect one row).
select column_name, data_type
from information_schema.columns
where table_schema = 'public' and table_name = 'opportunities' and column_name = 'posting_text';
