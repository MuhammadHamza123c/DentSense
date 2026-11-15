-- =============================================================================
-- CarDamageAI - report label + detection image URL
-- Tables: public.damage_reports, public.damage_detections
--
-- Run this in Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Safe to re-run: uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS guards.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. damage_reports: optional user-facing label (company / model / anything).
--    NULL when the client does not send one.
-- -----------------------------------------------------------------------------
alter table public.damage_reports
    add column if not exists label text;

alter table public.damage_reports
    drop constraint if exists damage_reports_label_len_chk;
alter table public.damage_reports
    add constraint damage_reports_label_len_chk
    check (label is null or char_length(btrim(label)) between 1 and 120);

comment on column public.damage_reports.label is
    'Optional user-facing label, e.g. company name or vehicle model. NULL when not provided.';

-- -----------------------------------------------------------------------------
-- 2. damage_detections: store the photo URL alongside each box so a draw-box
--    request can return image_url together with user_id and report_id.
-- -----------------------------------------------------------------------------
alter table public.damage_detections
    add column if not exists image_url text;

alter table public.damage_detections
    drop constraint if exists damage_detections_image_url_len_chk;
alter table public.damage_detections
    add constraint damage_detections_image_url_len_chk
    check (image_url is null or char_length(image_url) between 1 and 2048);

comment on column public.damage_detections.image_url is
    'Public Supabase Storage URL of the photo this box belongs to.';
