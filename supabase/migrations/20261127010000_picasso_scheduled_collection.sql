alter table public.picasso_runs
  add column scheduled_at timestamptz,
  add column failure_stage text;

create unique index picasso_scheduled_occurrence on public.picasso_runs (scheduled_at) where scheduled_at is not null;

-- Claim the due time and advance the rule together. The one-active-run index also covers manual runs.
create function public.picasso_claim_scheduled(expected_rule jsonb, following_rule jsonb)
returns uuid
language plpgsql
set search_path = ''
as $$
declare
  rule jsonb;
  due_at timestamptz;
  run_id uuid;
begin
  select value into rule from public.picasso_settings where name = 'schedule' for update;
  if not found or rule is distinct from expected_rule or rule->>'enabled' is distinct from 'true' then
    return null;
  end if;
  due_at := (rule->>'next_at')::timestamptz;
  if due_at is null or due_at > now() then
    return null;
  end if;

  insert into public.picasso_runs (scheduled_at, prior_run) values (
    due_at, (select id from public.picasso_runs where status = 'complete' order by created_at desc limit 1)
  ) returning id into run_id;
  update public.picasso_settings set value = following_rule where name = 'schedule';
  return run_id;
exception when unique_violation then
  return null;
end;
$$;

revoke execute on function public.picasso_claim_scheduled(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.picasso_claim_scheduled(jsonb, jsonb) to service_role;

alter table public.picasso_settings drop constraint picasso_settings_name_check;
alter table public.picasso_settings add constraint picasso_settings_name_check
  check (name in ('schedule', 'reference', 'scheduler_ready'));
