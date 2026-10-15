-- EDIT the email, then run only for a failed test profile after fixing the cause.
-- This queues a new paid model call when the worker runs.
update public.alumni_intake_responses
set tagging_status = 'pending', attempts = 0, lease_token = null, lease_until = null,
    last_error = null, updated_at = now()
where email = 'test1@example.com' and tagging_status = 'failed';
