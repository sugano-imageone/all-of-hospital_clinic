-- READ ONLY. Run in Supabase SQL Editor and share the single JSON result.
-- Contains table definitions/constraints/policies only; no rows or credential values.
with target_tables as (
 select c.oid,c.relname,c.relrowsecurity,c.relforcerowsecurity
 from pg_catalog.pg_class c
 join pg_catalog.pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and c.relname in
 ('medical_institutions','institution_beds','institution_changes','source_imports','app_members')
 and c.relkind in ('r','p')
)
select jsonb_build_object(
 'server_version',current_setting('server_version'),
 'tables',coalesce((select jsonb_agg(jsonb_build_object(
   'name',t.relname,'rls_enabled',t.relrowsecurity,'rls_forced',t.relforcerowsecurity,
   'columns',(select jsonb_agg(jsonb_build_object(
      'name',a.attname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
      'not_null',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated,
      'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid)) order by a.attnum)
      from pg_catalog.pg_attribute a
      left join pg_catalog.pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
      where a.attrelid=t.oid and a.attnum>0 and not a.attisdropped),
   'constraints',coalesce((select jsonb_agg(jsonb_build_object(
      'name',c.conname,'type',c.contype,'definition',pg_catalog.pg_get_constraintdef(c.oid,true)))
      from pg_catalog.pg_constraint c where c.conrelid=t.oid),'[]'::jsonb),
   'policies',coalesce((select jsonb_agg(jsonb_build_object(
      'name',p.policyname,'roles',p.roles,'command',p.cmd,'permissive',p.permissive,
      'using',p.qual,'with_check',p.with_check))
      from pg_catalog.pg_policies p where p.schemaname='public' and p.tablename=t.relname),'[]'::jsonb),
   'indexes',coalesce((select jsonb_agg(jsonb_build_object('name',i.indexname,'definition',i.indexdef))
      from pg_catalog.pg_indexes i where i.schemaname='public' and i.tablename=t.relname),'[]'::jsonb),
   'triggers',coalesce((select jsonb_agg(jsonb_build_object(
      'name',tr.tgname,'definition',pg_catalog.pg_get_triggerdef(tr.oid,true)))
      from pg_catalog.pg_trigger tr where tr.tgrelid=t.oid and not tr.tgisinternal),'[]'::jsonb)
 ) order by t.relname) from target_tables t),'[]'::jsonb)
) as schema_inspection;
