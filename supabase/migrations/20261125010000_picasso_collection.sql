-- Server-side data for PICasso KPI. No direct browser access.
create table public.picasso_runs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'queued' check (status in ('queued', 'running', 'complete', 'failed')),
  expected_count integer not null default 0 check (expected_count >= 0),
  error text
);

-- A repeated click can reuse the one active run.
create unique index picasso_one_active_run on public.picasso_runs ((true))
  where status in ('queued', 'running');

create table public.picasso_products (
  run_id uuid not null references public.picasso_runs(id) on delete cascade,
  brand text not null check (brand in ('SPAO', 'WHO')),
  product_id text not null,
  result jsonb not null check (jsonb_typeof(result) = 'object'),
  primary key (run_id, brand, product_id)
);

alter table public.picasso_runs enable row level security;
alter table public.picasso_products enable row level security;
revoke all on public.picasso_runs, public.picasso_products from anon, authenticated;
grant select, insert, update on public.picasso_runs, public.picasso_products to service_role;

-- The conditional update and count run in a single statement. Concurrent or
-- duplicate messages can safely attempt to finalize the run again.
create function public.picasso_finalize_run(target_run uuid)
returns boolean
language sql
set search_path = ''
as $$
  with completed as (
    update public.picasso_runs as run
       set status = 'complete', finished_at = now()
     where run.id = target_run and run.status = 'running' and run.expected_count > 0
       and run.expected_count = (
        select count(*) from public.picasso_products as product where product.run_id = target_run
       )
    returning run.id
  )
  select exists(select 1 from completed);
$$;

revoke execute on function public.picasso_finalize_run(uuid) from public, anon, authenticated;
grant execute on function public.picasso_finalize_run(uuid) to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('picasso-private', 'picasso-private', false, 8388608, array['image/jpeg', 'image/png'])
on conflict (id) do nothing;
