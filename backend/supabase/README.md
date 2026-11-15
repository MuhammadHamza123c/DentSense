# Supabase setup

Project URL: `https://ivdlmmkijtbcrzhiaypp.supabase.co`

## 1. Run the migrations

Supabase Dashboard → **SQL Editor** → **New query** → run these in order:

1. `migrations/20261113000100_damage_reports.sql` — reports table, indexes, RLS, storage bucket + policies.
2. `migrations/20261113000300_profiles.sql` — profiles table, RLS, auto-create trigger.
3. `migrations/20261113000200_auth_auto_confirm.sql` — **optional** auto-confirm trigger (see below).

All files are idempotent and can be re-run. `profiles` depends on
`public.set_updated_at()` from the first migration.

## 2. Turn off email confirmation (sign-up returns a session immediately)

Dashboard → **Authentication** → **Sign In / Providers** → **Email**:

- Turn **OFF** “Confirm email”.
- Keep “Email” provider enabled.
- Save.

Optional DB-level backup: run `20261113000200_auth_auto_confirm.sql`. It sets
`email_confirmed_at` on every new `auth.users` row. Use only if you do not need
verified email addresses.

## 3. Table shapes

### `public.damage_reports`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `uuid` | PK, `gen_random_uuid()` |
| `user_id` | `uuid` | FK → `auth.users(id)`, `on delete cascade` |
| `name` | `text` | 1–120 chars |
| `lat` | `double precision` | −90…90 |
| `lng` | `double precision` | −180…180 (“lang” in the UI) |
| `image_url` | `text` | Public storage URL, 1–2048 chars |
| `created_at` | `timestamptz` | `now()` |
| `updated_at` | `timestamptz` | maintained by trigger |

RLS is enabled. `anon` has **no** access; `authenticated` can only
select/insert/update/delete rows where `user_id = auth.uid()`.

### `public.profiles`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `uuid` | PK, FK → `auth.users(id)`, `on delete cascade` |
| `full_name` | `text` | nullable, 1–120 chars |
| `phone` | `text` | nullable, `+` and digits/spaces/dashes/parentheses |
| `created_at` | `timestamptz` | `now()` |
| `updated_at` | `timestamptz` | maintained by trigger |

A trigger on `auth.users` inserts a profile for every new signup. Users may only
`select`/`update` their own row; they cannot insert or delete. The backend uses
the service role for the idempotent upsert after signup.

## 4. Authentication flow (backend)

```
POST /api/v1/auth/signup  -> Supabase /auth/v1/signup -> session tokens
POST /api/v1/auth/login   -> Supabase /auth/v1/token?grant_type=password
POST /api/v1/auth/refresh -> Supabase /auth/v1/token?grant_type=refresh_token
POST /api/v1/auth/logout  -> Supabase /auth/v1/logout (revokes session)
GET  /api/v1/auth/me      -> Bearer token verified via Supabase /auth/v1/user
```

The backend keeps no auth session state; every protected request re-verifies the
token against GoTrue, so revoked/expired tokens are rejected immediately.

## 5. Storage

- Bucket: `damage-images`
- Public read: **yes** (needed for WhatsApp click-to-chat image links)
- Max file size: 10 MB
- Allowed MIME: `image/jpeg`, `image/jpg`, `image/png`, `image/webp`
- Write path **must** be `<user_id>/<filename>`

Upload example (frontend, authenticated):

```js
const path = `${user.id}/${crypto.randomUUID()}.jpg`;
const { error } = await supabase.storage
  .from("damage-images")
  .upload(path, file, { contentType: file.type, upsert: false });

const { data } = supabase.storage.from("damage-images").getPublicUrl(path);
const imageUrl = data.publicUrl;
```

Insert example (frontend):

```js
const { data, error } = await supabase
  .from("damage_reports")
  .insert({ user_id: user.id, name, lat, lng, image_url: imageUrl })
  .select()
  .single();
```

`user_id` must equal the signed-in user's id; the insert policy rejects
anything else. From the backend, the service-role key bypasses RLS, so the
backend must always derive `user_id` from the verified JWT — never from the
request body.

## 6. Verify

```sql
-- RLS enabled?
select relname, relrowsecurity
from pg_class
where oid = 'public.damage_reports'::regclass;

-- Policies present?
select policyname, cmd, roles
from pg_policies
where schemaname = 'public' and tablename = 'damage_reports'
order by cmd;

-- Bucket present?
select id, name, public, file_size_limit, allowed_mime_types
from storage.buckets
where id = 'damage-images';

-- Storage policies present?
select policyname, cmd
from pg_policies
where schemaname = 'storage' and tablename = 'objects'
  and policyname like 'damage_images_%'
order by policyname;

-- Profiles table + trigger
select policyname, cmd
from pg_policies
where schemaname = 'public' and tablename = 'profiles'
order by cmd;

select tgname from pg_trigger
where tgrelid = 'public.profiles'::regclass and not tgisinternal;

select tgname from pg_trigger
where tgrelid = 'auth.users'::regclass and not tgisinternal;
```

Expected: `relrowsecurity = true`, four `damage_reports_*` policies, three
`damage_images_*` policies, one `damage-images` bucket with `public = true`,
two `profiles_*` policies, the `profiles_set_updated_at` trigger, and
`on_auth_user_created` (plus `auto_confirm_new_user_email` if you ran the
optional file).

## 7. Security notes

- A public bucket means anyone holding an object URL can view that image.
  Object names contain a UUID, which makes guessing impractical, but the URL is
  not access-controlled. Do not store sensitive documents there.
- To switch to private images later: set `public = false`, remove the public
  URL assumption, and serve short-lived signed URLs from the backend.
- Never use `SUPABASE_SECRET_KEY` (service role) in the frontend. Only the
  publishable/anon key belongs there.
- Never send `user_id` from an untrusted client to the backend; resolve it from
  `Authorization: Bearer <access_token>`.
