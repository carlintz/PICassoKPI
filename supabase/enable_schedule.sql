-- Provision the picasso_scheduler_key secret in Vault before enabling the production job.
begin;
create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.picasso_dispatch_schedule()
returns void
language plpgsql
set search_path = ''
as $$
declare
  scheduler_key text;
begin
  if not exists (select 1 from public.picasso_runs where status = 'queued' and scheduled_at is not null)
     and not (
       exists (select 1 from public.picasso_settings where name = 'schedule' and value->>'enabled' = 'true'
                 and (value->>'next_at')::timestamptz <= now())
       and not exists (select 1 from public.picasso_runs where status in ('queued', 'running'))
     ) then
    return;
  end if;
  select decrypted_secret into scheduler_key from vault.decrypted_secrets where name = 'picasso_scheduler_key';
  if scheduler_key is null then
    raise exception 'PICasso scheduler key is missing';
  end if;
  perform net.http_post(url := 'https://picasso-kpi.vercel.app/api/schedule/tick',
    headers := jsonb_build_object('Authorization', 'Bearer ' || scheduler_key, 'Content-Type', 'application/json'),
    timeout_milliseconds := 30000);
end;
$$;
revoke execute on function public.picasso_dispatch_schedule() from public, anon, authenticated;

do $$
begin
  if not exists (select 1 from vault.decrypted_secrets where name = 'picasso_scheduler_key') then
    raise exception 'PICasso scheduler key is missing';
  end if;
  perform cron.schedule('picasso-schedule-tick', '* * * * *', 'select public.picasso_dispatch_schedule()');
  insert into public.picasso_settings(name,value) values ('scheduler_ready', 'true'::jsonb)
    on conflict (name) do update set value=excluded.value;
end;
$$;
commit;
