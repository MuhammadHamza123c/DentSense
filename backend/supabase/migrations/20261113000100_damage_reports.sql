-- =============================================================================
-- CarDamageAI - initial schema
-- Table: public.damage_reports
-- Bucket: damage-images (public read, per-user write)
--
-- Run this in Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Safe to re-run: uses IF NOT EXISTS / DROP POLICY IF EXISTS guards.
-- =============================================================================

-- Needed for gen_random_uuid() on fresh projects (already enabled on Supabase).
create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- 1. Table
-- -----------------------------------------------------------------------------
create table if not exists public.damage_reports (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    name        text not null,
    lat         double precision not null,
    lng         double precision not null,
    image_url   text not null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    constraint damage_reports_name_len_chk
        check (char_length(btrim(name)) between 1 and 120),
    constraint damage_reports_lat_chk
        check (lat between -90 and 90),
    constraint damage_reports_lng_chk
        check (lng between -180 and 180),
    constraint damage_reports_image_url_len_chk
        check (char_length(image_url) between 1 and 2048)
);

comment on table  public.damage_reports is
    'User-owned car damage reports. Location is the browser-provided lat/lng.';
comment on column public.damage_reports.name is
    'User-facing label, e.g. vehicle name or report title.';
comment on column public.damage_reports.lat is
    'Latitude in decimal degrees (-90..90).';
comment on column public.damage_reports.lng is
    'Longitude in decimal degrees (-180..180). "lang" in the UI maps to this.';
comment on column public.damage_reports.image_url is
    'Public Supabase Storage URL: https://<project>.supabase.co/storage/v1/object/public/damage-images/<user_id>/<file>';

-- Fast "my reports" listing, newest first.
create index if not exists damage_reports_user_created_idx
    on public.damage_reports (user_id, created_at desc);

-- -----------------------------------------------------------------------------
-- 2. Keep updated_at fresh
-- -----------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists damage_reports_set_updated_at on public.damage_reports;
create trigger damage_reports_set_updated_at
    before update on public.damage_reports
    for each row
    execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- 3. Row Level Security (RLS)
--    user_id is always derived from the JWT via auth.uid(); the client cannot
--    read or write another user's rows.
-- -----------------------------------------------------------------------------
alter table public.damage_reports enable row level security;

revoke all on public.damage_reports from anon;
grant select, insert, update, delete on public.damage_reports to authenticated;

drop policy if exists "damage_reports_select_own" on public.damage_reports;
create policy "damage_reports_select_own"
    on public.damage_reports
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists "damage_reports_insert_own" on public.damage_reports;
create policy "damage_reports_insert_own"
    on public.damage_reports
    for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists "damage_reports_update_own" on public.damage_reports;
create policy "damage_reports_update_own"
    on public.damage_reports
    for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

drop policy if exists "damage_reports_delete_own" on public.damage_reports;
create policy "damage_reports_delete_own"
    on public.damage_reports
    for delete
    to authenticated
    using ((select auth.uid()) = user_id);

-- -----------------------------------------------------------------------------
-- 4. Storage bucket
--    Public read is required for sharing images via WhatsApp click-to-chat.
--    Writes are restricted to a per-user folder: <user_id>/<file>.
-- -----------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'damage-images',
    'damage-images',
    true,
    10485760, -- 10 MB, matches MAX_UPLOAD_SIZE_MB
    array['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
)
on conflict (id) do nothing;

-- If the bucket already existed, make sure limits still match:
update storage.buckets
set public = true,
    file_size_limit = 10485760,
    allowed_mime_types = array['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
where id = 'damage-images';

drop policy if exists "damage_images_insert_own_folder" on storage.objects;
create policy "damage_images_insert_own_folder"
    on storage.objects
    for insert
    to authenticated
    with check (
        bucket_id = 'damage-images'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    );

drop policy if exists "damage_images_update_own_folder" on storage.objects;
create policy "damage_images_update_own_folder"
    on storage.objects
    for update
    to authenticated
    using (
        bucket_id = 'damage-images'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    )
    with check (
        bucket_id = 'damage-images'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    );

drop policy if exists "damage_images_delete_own_folder" on storage.objects;
create policy "damage_images_delete_own_folder"
    on storage.objects
    for delete
    to authenticated
    using (
        bucket_id = 'damage-images'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    );

-- Public bucket reads are served by /storage/v1/object/public/... without a
-- SELECT policy. Private buckets would need signed URLs instead.
