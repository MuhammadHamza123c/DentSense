-- =============================================================================
-- OPTIONAL: auto-confirm email addresses on sign-up
--
-- Preferred approach: Dashboard -> Authentication -> Sign In / Providers ->
-- Email -> turn OFF "Confirm email". That is the officially supported switch.
--
-- Run this ONLY if you also want a database-level guarantee (e.g. when the
-- dashboard toggle is accidentally re-enabled). It marks new users as
-- confirmed at insert time, so signUp() returns a session immediately.
--
-- Warning: this bypasses email verification for every account. Do not use it
-- for an app that needs verified email addresses.
-- =============================================================================

create or replace function public.auto_confirm_new_user_email()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.email_confirmed_at is null then
        new.email_confirmed_at := now();
        new.confirmed_at := now();
    end if;
    return new;
end;
$$;

drop trigger if exists auto_confirm_new_user_email on auth.users;
create trigger auto_confirm_new_user_email
    before insert on auth.users
    for each row
    execute function public.auto_confirm_new_user_email();
