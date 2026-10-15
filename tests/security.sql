-- Run in the isolated integration DB after migrations; no production mutations.
do $$ declare t text; begin
 foreach t in array array['medical_institutions','institution_beds','institution_changes','source_imports','app_members','institution_links','access_logs','update_jobs'] loop
  if not (select relrowsecurity from pg_class where oid=('public.'||t)::regclass) then raise exception 'RLS missing: %',t; end if;
  if has_table_privilege('anon','public.'||t,'select') then raise exception 'anonymous read: %',t; end if;
  if has_table_privilege('authenticated','public.'||t,'insert,update,delete') then raise exception 'direct write: %',t; end if;
 end loop;
 if has_function_privilege('authenticated','public.import_snapshot(jsonb,jsonb)','execute') then raise exception 'import accessible'; end if;
 if has_function_privilege('anon','public.record_access(text,jsonb)','execute') then raise exception 'audit accessible'; end if;
end $$;
