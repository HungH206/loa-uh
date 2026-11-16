-- Run in the Supabase project's SQL Editor. Idempotent for this migration.
begin;
create table if not exists public.incident_reports (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    request_id uuid not null,
    occurred_at timestamptz not null,
    created_at timestamptz not null default now(),
    location text not null check (char_length(btrim(location)) between 1 and 200),
    description text not null check (char_length(btrim(description)) between 1 and 2000),
    feeling text not null check (feeling in ('Tolerable', 'Need to Cope', 'Sensitive')),
    details text not null default '' check (char_length(details) <= 4000),
    unique (user_id, request_id)
);
create index if not exists incident_reports_user_time
    on public.incident_reports (user_id, occurred_at desc, id desc);
alter table public.incident_reports enable row level security;
revoke all on public.incident_reports from anon, authenticated;
grant select, insert on public.incident_reports to authenticated;
drop policy if exists incident_reports_read_own on public.incident_reports;
create policy incident_reports_read_own on public.incident_reports
    for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists incident_reports_insert_own on public.incident_reports;
create policy incident_reports_insert_own on public.incident_reports
    for insert to authenticated with check ((select auth.uid()) = user_id);
comment on table public.incident_reports is 'LOA personal noise disturbance reports; sensor readings remain on the Pi.';
notify pgrst, 'reload schema';
commit;
