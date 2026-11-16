begin;
create table if not exists public.user_preferences (
 user_id uuid primary key references auth.users(id) on delete cascade,
 threshold double precision not null default -40 check(threshold between -90 and -1),
 duration_seconds integer not null default 3 check(duration_seconds between 1 and 60),
 cooldown_seconds integer not null default 30 check(cooldown_seconds between 0 and 3600)
);
alter table public.user_preferences enable row level security;
revoke all on public.user_preferences from anon, authenticated;
grant select,insert,update on public.user_preferences to authenticated;
drop policy if exists preferences_read_own on public.user_preferences;
create policy preferences_read_own on public.user_preferences for select to authenticated using ((select auth.uid())=user_id);
drop policy if exists preferences_insert_own on public.user_preferences;
create policy preferences_insert_own on public.user_preferences for insert to authenticated with check ((select auth.uid())=user_id);
drop policy if exists preferences_update_own on public.user_preferences;
create policy preferences_update_own on public.user_preferences for update to authenticated using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id);
notify pgrst, 'reload schema';
commit;
