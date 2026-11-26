create table public.picasso_references (
  brand text not null check (brand in ('SPAO', 'WHO')),
  reference_group text not null check (reference_group in ('men', 'women')),
  storage_path text not null check (storage_path ~ '^reference/[a-f0-9]{64}\.jpg$'),
  embedding jsonb not null check (jsonb_typeof(embedding) = 'array' and jsonb_array_length(embedding) = 128),
  primary key (brand, reference_group, storage_path)
);

alter table public.picasso_references enable row level security;
revoke all on public.picasso_references from public, anon, authenticated;
grant select, insert on public.picasso_references to service_role;

alter table public.picasso_runs
  add column reference_sets jsonb check (jsonb_typeof(reference_sets) = 'array');
