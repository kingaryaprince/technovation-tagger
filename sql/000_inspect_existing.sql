-- Read-only metadata. Safe to share output for integration help; no profile data selected.
select column_name, data_type, udt_name, is_nullable, column_default
from information_schema.columns
where table_schema='public' and table_name='alumni_profiles'
order by ordinal_position;

select con.conname, pg_get_constraintdef(con.oid) as definition
from pg_constraint con join pg_class rel on rel.oid=con.conrelid
join pg_namespace ns on ns.oid=rel.relnamespace
where ns.nspname='public' and rel.relname='alumni_profiles';
