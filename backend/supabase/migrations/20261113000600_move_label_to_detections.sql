-- =============================================================================
-- CarDamageAI - move label from damage_reports to damage_detections
-- =============================================================================

-- 1. Remove label from damage_reports
alter table public.damage_reports
    drop column if exists label;
alter table public.damage_reports
    drop constraint if exists damage_reports_label_len_chk;

-- 2. Add label to damage_detections
alter table public.damage_detections
    add column if not exists label text;

alter table public.damage_detections
    drop constraint if exists damage_detections_label_len_chk;
alter table public.damage_detections
    add constraint damage_detections_label_len_chk
    check (label is null or char_length(btrim(label)) between 1 and 120);

comment on column public.damage_detections.label is
    'Car brand / company selected by the user for this detection (e.g. Toyota, Suzuki).';
