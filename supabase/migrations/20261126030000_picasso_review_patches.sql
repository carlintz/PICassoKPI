-- A selection can require different field updates for products with and without a detected model.
create or replace function public.picasso_review_products(
  target_run uuid, expected_version bigint, products jsonb, changes jsonb, report jsonb
) returns boolean
language plpgsql
set search_path = ''
as $$
declare
  current_version bigint;
  updated_count integer;
begin
  select run.review_version into current_version
  from public.picasso_runs as run
  where run.id = target_run and run.status = 'complete'
  for update;

  if not found or current_version <> expected_version then
    return false;
  end if;

  update public.picasso_products as product
  set result = product.result || coalesce(selected.patch, changes)
  from jsonb_to_recordset(products) as selected(brand text, product_id text, patch jsonb)
  where product.run_id = target_run and product.brand = selected.brand
    and product.product_id = selected.product_id;

  get diagnostics updated_count = row_count;
  if updated_count <> jsonb_array_length(products) then
    raise exception 'Review contains unknown or duplicate products';
  end if;

  update public.picasso_runs
  set summary = report, review_version = current_version + 1
  where id = target_run;

  return true;
end;
$$;
