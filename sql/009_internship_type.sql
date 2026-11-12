-- Allow type = 'internship' on opportunities, for listings imported by
-- alumni_tagger/job_import.py. Run once in the Supabase SQL editor; safe to re-run.
--
-- Rather than retyping the allowed list (and risking dropping a value), this reads the
-- current opportunities_type_check and adds 'internship' to it. It stops with an error,
-- changing nothing, if the rule isn't the usual "type = ANY (ARRAY[...])" shape.

do $$
declare
  old_def text;
  new_def text;
begin
  select pg_get_constraintdef(oid) into old_def
  from pg_constraint
  where conrelid = 'public.opportunities'::regclass and conname = 'opportunities_type_check';

  if old_def is null then
    raise exception 'opportunities_type_check not found; nothing changed';
  elsif old_def like '%''internship''%' then
    raise notice 'internship already allowed: %', old_def;
    return;
  elsif old_def !~ 'ARRAY\[' then
    raise exception 'Unexpected rule shape, edit by hand: %', old_def;
  end if;

  new_def := regexp_replace(old_def, 'ARRAY\[', 'ARRAY[''internship''::text, ');
  execute 'alter table public.opportunities drop constraint opportunities_type_check';
  execute 'alter table public.opportunities add constraint opportunities_type_check ' || new_def;
  raise notice 'was: %', old_def;
  raise notice 'now: %', new_def;
end $$;

-- Check: shows the rule now in force.
select pg_get_constraintdef(oid) as type_rule
from pg_constraint
where conrelid = 'public.opportunities'::regclass and conname = 'opportunities_type_check';
