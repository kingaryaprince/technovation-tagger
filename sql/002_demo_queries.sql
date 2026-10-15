-- Read-only examples for Supabase SQL Editor. Run blocks individually.
select id, email, full_name, tagging_status, revision, attempts, tags, last_error
from public.alumni_intake_responses order by updated_at desc;

select full_name, email, tags->'skills' as skills
from public.alumni_intake_responses
where tagging_status = 'completed' and tags @> '{"skills":["Python"]}'::jsonb;

select full_name, email, tags->'interests' as interests
from public.alumni_intake_responses
where tagging_status = 'completed' and tags @> '{"interests":["AI/Machine Learning"]}'::jsonb;

-- Same-category multiple tags means AND. OR example below.
select full_name, email from public.alumni_intake_responses
where tagging_status = 'completed'
and (tags @> '{"interests":["AgTech"]}'::jsonb
     or tags @> '{"interests":["Climate Tech"]}'::jsonb);

-- Structured grid answers are preserved as row-label -> answer objects.
select full_name, raw_answers->'topic_familiarity', raw_answers->'interview_comfort'
from public.alumni_intake_responses;

-- Optional bridge to the existing week-two table, if it has an email column:
-- select p.*, i.tags from public.alumni_profiles p
-- join public.alumni_intake_responses i on lower(btrim(p.email)) = i.email
-- where i.tagging_status = 'completed';
-- Only matching emails appear. New-form respondents are not inserted into alumni_profiles by this prototype.
