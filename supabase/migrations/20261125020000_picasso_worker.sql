alter table public.picasso_runs
  add column manifest jsonb,
  add column reference_path text,
  add column prior_run uuid references public.picasso_runs(id),
  add column summary jsonb;

create table public.picasso_settings (
  name text primary key check (name in ('schedule', 'reference')),
  value jsonb not null
);
alter table public.picasso_settings enable row level security;
revoke all on public.picasso_settings from anon, authenticated;
grant select, insert, update on public.picasso_settings to service_role;

create function public.picasso_progress(target_run uuid)
returns integer
language sql stable
set search_path = ''
as $$
  select count(*)::integer from public.picasso_products where run_id = target_run
$$;
revoke execute on function public.picasso_progress(uuid) from public, anon, authenticated;
grant execute on function public.picasso_progress(uuid) to service_role;

-- Finish only after every distinct product is present. The summary and status are published together.
create function public.picasso_complete(target_run uuid, report jsonb)
returns boolean
language sql
set search_path = ''
as $$
  with completed as (
    update public.picasso_runs set status='complete', finished_at=now(), summary=report, manifest=null
    where id=target_run and status='running' and expected_count>0
      and expected_count=public.picasso_progress(target_run)
    returning id
  ) select exists(select 1 from completed)
$$;
revoke execute on function public.picasso_complete(uuid,jsonb) from public, anon, authenticated;
grant execute on function public.picasso_complete(uuid,jsonb) to service_role;
