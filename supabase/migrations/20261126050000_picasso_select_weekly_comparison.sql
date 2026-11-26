create function public.picasso_weekly_new_products(target_run uuid, comparison_run uuid)
returns jsonb
language sql stable
set search_path = ''
as $$
  with anchor as (
    select id, created_at, created_at - interval '168 hours' as window_start
    from public.picasso_runs where id = target_run and status = 'complete'
  ), comparison as (
    select r.created_at from public.picasso_runs r cross join anchor a
    where r.id = comparison_run and r.status = 'complete'
      and (r.created_at, r.id) < (a.created_at, a.id)
      and '00000000-0000-4000-8000-000000000001'::uuid not in (r.id, a.id)
  ), history as (
    select r.id, r.created_at, a.window_start,
           row_number() over (order by r.created_at, r.id) as ordinal
    from public.picasso_runs r cross join anchor a
    where r.status = 'complete' and (r.created_at, r.id) <= (a.created_at, a.id)
      and '00000000-0000-4000-8000-000000000001'::uuid not in (r.id, a.id)
      and (comparison_run is null or r.id in (target_run, comparison_run))
  ), periods as (
    select *, ordinal > 1 and (comparison_run is not null or created_at >= window_start) as recent from history
  ), observations as (
    select p.brand, p.product_id, p.run_id, r.created_at, r.recent,
           upper(btrim(coalesce(p.result->>'code', ''), E' \t\r\n')) as code,
           p.result->>'thumbnail_type' as thumbnail_type,
           p.result->>'similarity' as similarity, p.result->>'error' as error
    from periods r join public.picasso_products p on p.run_id = r.id
  ), known_styles as (
    select brand, code from observations where not recent and code <> ''
    union
    select n.brand, n.code from observations n
    where n.recent and n.code <> '' and exists (
      select 1 from observations p where not p.recent and p.code = ''
        and p.brand = n.brand and p.product_id = n.product_id
    )
  ), new_styles as (
    select distinct on (o.brand, o.code) o.brand, o.code, o.thumbnail_type, o.similarity, o.error
    from observations o
    where o.recent and o.code <> '' and not exists (
      select 1 from known_styles k where k.brand = o.brand and k.code = o.code
    )
    order by o.brand, o.code, o.created_at desc, o.run_id desc,
             (o.thumbnail_type in ('model', 'product_only')) desc, o.product_id
  )
  select jsonb_build_object(
    'end_at', a.created_at,
    'window_start', coalesce((select created_at from comparison), a.window_start),
    'baseline_at', (select max(created_at) from periods where not recent and id <> a.id),
    'products', coalesce((select jsonb_agg(to_jsonb(n)) from new_styles n), '[]'::jsonb)
  ) from anchor a
  where comparison_run is null or exists (select 1 from comparison)
$$;

revoke execute on function public.picasso_weekly_new_products(uuid, uuid) from public, anon, authenticated;
grant execute on function public.picasso_weekly_new_products(uuid, uuid) to service_role;

create or replace function public.picasso_weekly_new_products(target_run uuid)
returns jsonb
language sql stable
set search_path = ''
as $$
  select public.picasso_weekly_new_products(target_run, null::uuid)
$$;
