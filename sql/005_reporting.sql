-- Controlled metadata/aggregate access. Underlying source and change tables can
-- retain the inspected admin-only policies. No raw evidence/error bodies returned.
begin;
create or replace function public.source_metadata() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb;
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 select coalesce(jsonb_agg(to_jsonb(s) order by s.reference_date desc nulls last,s.id),'[]'::jsonb) into result
 from (select id,prefecture_code,prefecture,bureau,filename,source_url,sha256,archive_sha256,
 parser_version,reference_date,file_created_date,retrieved_at,status,record_count
 from public.source_imports) s;
 return result;
end $$;
revoke all on function public.source_metadata() from public,anon;
grant execute on function public.source_metadata() to authenticated;

create or replace function public.monthly_change_counts() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb;
begin
 if not public.is_approved() then raise exception 'approval required'; end if;
 with events as (
  select to_char(event_date,'YYYY-MM') as month,
   case event_type when 'new_designation' then '新規指定総数' when 'changed' then '変更数'
    when 'missing_candidate' then '廃止候補（未確定）' when 'closure_confirmed' then '廃止等確定数' end as category
  from public.institution_changes where event_type in ('new_designation','changed','missing_candidate','closure_confirmed') and event_date is not null
  union all
  select to_char(event_date,'YYYY-MM'),'純新規開業数' from public.institution_changes
  where event_type='new_designation' and opening_classification='純新規開業' and event_date is not null
 ), counts as (select month,category,count(*) as n from events group by month,category),
 months as (select month,jsonb_object_agg(category,n) as totals from counts group by month)
 select coalesce(jsonb_object_agg(month,totals),'{}'::jsonb) into result from months;
 return result;
end $$;
revoke all on function public.monthly_change_counts() from public,anon;
grant execute on function public.monthly_change_counts() to authenticated;
commit;
