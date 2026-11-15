-- =============================================================================
-- CarDamageAI - user profiles
--
-- One row per auth user, created automatically on sign-up by a trigger.
-- RLS: users may read and update only their own profile. Inserts are done by
-- the trigger (security definer) or by the backend service role.
-- =============================================================================

create table if not exists public.profiles (
    id          uuid primary key references auth.users (id) on delete cascade,
    full_name   text,
    phone       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    constraint profiles_full_name_len_chk
        check (full_name is null or char_length(btrim(full_name)) between 1 and 120),
    constraint profiles_phone_chk
        check (phone is null or phone ~ '^\+?[0-9][0-9 ()\-]{6,19}$')
);

comment on table public.profiles is 'Public profile data for each auth user.';

alter table public.profiles enable row level security;

revoke all on public.profiles from anon;
grant select, update on public.profiles to authenticated;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own"
    on public.profiles
    for select
    to authenticated
    using ((select auth.uid()) = id);

drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own"
    on public.profiles
    for update
    to authenticated
    using ((select auth.uid()) = id)
    with check ((select auth.uid()) = id);

-- There is intentionally no INSERT or DELETE policy for authenticated users.
-- Profile rows are created by the trigger below and removed via the FK cascade
-- when the auth user is deleted.

-- Keep updated_at fresh (function created in 20261113000100_damage_reports.sql).
drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
    before update on public.profiles
    for each row
    execute function public.set_updated_at();

-- Auto-create a profile for every new auth user.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, full_name)
    values (
        new.id,
        nullif(btrim(new.raw_user_meta_data ->> 'full_name'), '')
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute function public.handle_new_user();

-- Backfill existing users (safe to re-run).
insert into public.profiles (id)
select id from auth.users
on conflict (id) do nothing;
