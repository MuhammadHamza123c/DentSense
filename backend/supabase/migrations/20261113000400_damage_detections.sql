-- =============================================================================
-- CarDamageAI - damage detections
-- Table: public.damage_detections (child of public.damage_reports)
--
-- Run this in Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Safe to re-run: uses IF NOT EXISTS / IF EXISTS guards.
--
-- Every row is one damage box returned by Roboflow for one uploaded photo:
--   report_id  -> public.damage_reports.id  (cascade delete)
--   user_id    -> the report owner (composite FK guarantees it matches)
--   x, y       -> box centre in image pixels
--   width,
--   height     -> box size in image pixels
--   damage_area_percentage -> (width * height) / (image_width * image_height) * 100
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Reports may exist without a car name or GPS location.
--    /damage/detect only knows the photo, so those values stay NULL unless the
--    client sends them as multipart fields.
-- -----------------------------------------------------------------------------
alter table public.damage_reports alter column name drop not null;
alter table public.damage_reports alter column lat  drop not null;
alter table public.damage_reports alter column lng  drop not null;

-- Composite key so a detection can reference (report id, owner) in one FK and
-- therefore can never point at a report owned by a different user.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.damage_reports'::regclass
          and conname = 'damage_reports_id_user_key'
    ) then
        alter table public.damage_reports
            add constraint damage_reports_id_user_key unique (id, user_id);
    end if;
end $$;

-- -----------------------------------------------------------------------------
-- 2. Table
-- -----------------------------------------------------------------------------
create table if not exists public.damage_detections (
    id                     uuid primary key default gen_random_uuid(),
    report_id              uuid not null,
    user_id                uuid not null,
    damage_class           text not null,
    confidence             double precision,
    class_id               integer,
    detection_id           text,
    x                      double precision not null,
    y                      double precision not null,
    width                  double precision not null,
    height                 double precision not null,
    damage_area_percentage double precision not null,
    created_at             timestamptz not null default now(),

    constraint damage_detections_report_owner_fk
        foreign key (report_id, user_id)
        references public.damage_reports (id, user_id)
        on delete cascade,

    constraint damage_detections_class_len_chk
        check (char_length(btrim(damage_class)) between 1 and 64),
    constraint damage_detections_confidence_chk
        check (confidence is null or confidence between 0 and 1),
    constraint damage_detections_class_id_chk
        check (class_id is null or class_id >= 0),
    constraint damage_detections_detection_id_len_chk
        check (detection_id is null or char_length(detection_id) between 1 and 128),
    constraint damage_detections_box_chk
        check (x >= 0 and y >= 0 and width > 0 and height > 0),
    constraint damage_detections_area_chk
        check (damage_area_percentage between 0 and 100)
);

comment on table public.damage_detections is
    'One Roboflow damage box per row, linked to the uploaded photo report.';
comment on column public.damage_detections.report_id is
    'Parent photo report: public.damage_reports.id.';
comment on column public.damage_detections.user_id is
    'Report owner. Composite FK (report_id, user_id) prevents cross-user links.';
comment on column public.damage_detections.x is
    'Detection box centre X in image pixels.';
comment on column public.damage_detections.y is
    'Detection box centre Y in image pixels.';
comment on column public.damage_detections.width is
    'Detection box width in image pixels.';
comment on column public.damage_detections.height is
    'Detection box height in image pixels.';
comment on column public.damage_detections.damage_area_percentage is
    'Box area as a percentage of the whole photo: (width*height)/(image_width*image_height)*100.';

create index if not exists damage_detections_report_idx
    on public.damage_detections (report_id);
create index if not exists damage_detections_user_created_idx
    on public.damage_detections (user_id, created_at desc);

-- -----------------------------------------------------------------------------
-- 3. Row Level Security (RLS)
--    The backend uses the service role, but RLS still protects direct client
--    access through Supabase: a user only sees their own detections.
-- -----------------------------------------------------------------------------
alter table public.damage_detections enable row level security;

revoke all on public.damage_detections from anon;
grant select, insert, delete on public.damage_detections to authenticated;

drop policy if exists "damage_detections_select_own" on public.damage_detections;
create policy "damage_detections_select_own"
    on public.damage_detections
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists "damage_detections_insert_own" on public.damage_detections;
create policy "damage_detections_insert_own"
    on public.damage_detections
    for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists "damage_detections_delete_own" on public.damage_detections;
create policy "damage_detections_delete_own"
    on public.damage_detections
    for delete
    to authenticated
    using ((select auth.uid()) = user_id);
