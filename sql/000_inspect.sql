-- Run FIRST in Supabase SQL Editor. Read-only. Share column metadata, never data/secrets.
select table_schema,table_name,column_name,data_type,is_nullable
from information_schema.columns
where table_schema='public' and table_name in
 ('medical_institutions','institution_beds','institution_changes','source_imports','app_members','institution_links','access_logs')
order by table_name,ordinal_position;
select schemaname,tablename,policyname,roles,cmd,qual,with_check from pg_policies where schemaname='public';
select tablename,rowsecurity from pg_tables where schemaname='public';
