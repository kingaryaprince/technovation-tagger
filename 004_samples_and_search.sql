-- Supabase SQL Editor. Run ONE block at a time (highlight it, then Run).
-- Nothing here creates tables; 001_tagging.sql already did that.

-- 1. Status of every row: form responses and the 20 sample profiles.
select full_name, email, tagging_status, prompt_version, tagged_at
from public.alumni_intake_responses
order by created_at desc;

-- 2. Tags for the sample profiles, one row each.
select full_name, tags->'industry' as industry, tags->'career_stage' as stage,
       tags->'interests' as interests, tags->'skills' as skills, tags->'story_angles' as story
from public.alumni_intake_responses
where email like 'sample-%@example.com' and tagging_status = 'completed'
order by email;

-- 3. Demo searches. These match on MEANING: nobody typed the label itself.
-- 3a. Speakers with a career-change story ("went from engineering into consulting", "switched to industrial design")
select full_name, tags->'story_angles' as story
from public.alumni_intake_responses
where tags @> '{"story_angles":["Non-Linear Career Path/Pivots"]}';

-- 3b. People who want to mentor AND care about women in STEM
select full_name, tags->'interests' as interests
from public.alumni_intake_responses
where tags @> '{"interests":["Mentorship","Women in STEM"]}';

-- 3c. Anyone whose project was about the environment, with what they built
select full_name, tags->'project_domain' as domain, tags->'project_built_with' as built_with
from public.alumni_intake_responses
where tags @> '{"project_domain":["Environment/Sustainability"]}';

-- 3d. Real form responses only: interested in speaking + can give talks
select full_name, raw_answers->>'speaker_interest' as wants_to_speak, tags->'skills' as skills
from public.alumni_intake_responses
where email not like 'sample-%@example.com'
  and tags @> '{"skills":["Public Speaking"]}';

-- 4. Re-tag the samples after changing the prompt or taxonomy (then run the worker again).
update public.alumni_intake_responses
set tagging_status = 'pending', attempts = 0, lease_token = null, lease_until = null,
    tags = null, evidence = null, last_error = null, tagged_at = null,
    revision = revision + 1, updated_at = now()
where email like 'sample-%@example.com';

-- 5. Remove the samples before the pilot (only rows with sample-XX@example.com emails).
delete from public.alumni_intake_responses
where email like 'sample-%@example.com';
