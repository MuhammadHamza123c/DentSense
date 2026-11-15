# CarDamageAI — Backend

AI-powered car damage detection and repair-cost estimation API.

> **Backend only.** This repository intentionally contains no frontend, React,
> HTML, CSS or UI code. A separate React client can integrate with the REST API
> documented below.

> **Estimates, not inspections.** Damage detection is produced by a computer
> vision model and severity/cost figures are estimates. They are **not**
> mechanically verified assessments and **not** guaranteed mechanic quotations.

---

## Status

| Stage | Scope | State |
| ----- | ----- | ----- |
| 1 | Project inspection | ✅ complete |
| 2 | Backend structure, dependencies, environment config | ✅ complete |
| 3 | FastAPI app, logging, error handling, health endpoints | ✅ complete |
| 4 | Supabase schema (reports, profiles, storage, RLS) | ✅ complete |
| 5 | Authentication: signup/login/refresh/logout/me | ✅ complete |
| 6 | Roboflow damage detection | ✅ complete |
| 7 | Severity assessment | ⏳ next |
| 8 | Repair cost estimation | ⏳ planned |
| 9 | Nearby mechanics (Overpass/OSRM) | ⏳ planned |
| 10 | WhatsApp click-to-chat | ⏳ planned |
| 11 | Damage reports API | ⏳ planned |
| 12 | Full test suite run, docs, hardening | ⏳ planned |

---

## Tech stack

- **Python 3.11**, FastAPI, Uvicorn
- **Pydantic v2** + **pydantic-settings** for validated configuration
- **Roboflow Inference SDK** (`inference-sdk`) for hosted model inference
- **Supabase** (PostgreSQL + Auth + Storage) via `supabase-py`
- **HTTPX** for Overpass API and OSRM routing calls
- **OpenStreetMap / Overpass / OSRM** for free mechanic discovery and routing
- **WhatsApp Click-to-Chat** deep links (no paid WhatsApp Business API)

Free-tier services have quotas, rate limits and availability limits. The API
degrades with explicit `503`/`502` errors and never fabricates results. See
[Free-tier limitations](#free-tier-limitations).

---

## Architecture

```
backend/
├── app/
│   ├── main.py              # Application factory, middleware, routers
│   ├── config.py            # Typed settings (SecretStr for credentials)
│   ├── dependencies.py      # Auth / client dependency providers
│   ├── exceptions.py        # Domain errors + JSON error handlers
│   ├── logging_config.py    # Request-correlated logs, JSON mode, redaction
│   ├── middleware.py        # Request ID, security headers, body-size guard
│   ├── routes/              # HTTP layer only (thin controllers)
│   ├── services/            # Business logic + external API integrations
│   ├── repositories/        # Supabase/PostgREST data access
│   ├── models/              # Domain enums/value objects
│   ├── schemas/             # Pydantic request/response contracts
│   └── utils/               # Validation, formatting, phone helpers
├── tests/                   # pytest suite (external services mocked)
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml           # pytest / coverage / ruff configuration
├── .env.example
└── README.md
```

**Layering rule:** routes → services → repositories. Routes never call
Supabase or third-party APIs directly; services never build HTTP responses.

---

## Prerequisites

- Python **3.11.x**
- A Roboflow account with access to the existing model `car-damage-wpmh2/1`
  (no new model is trained)
- A Supabase project (PostgreSQL, Auth, Storage)
- Internet access for Overpass/OSRM calls (optional at runtime)

---

## Installation

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

macOS/Linux:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

---

## Environment configuration

```powershell
Copy-Item .env.example .env
```

Fill in `.env`. Never commit it (`.gitignore` already excludes `.env`).

| Variable | Required | Purpose |
| -------- | -------- | ------- |
| `ENVIRONMENT` | yes | `development` / `staging` / `production` |
| `DEBUG` | no | Verbose errors locally; forced off in production responses |
| `CORS_ORIGINS` | yes | CSV or JSON list of allowed frontend origins |
| `ROBOFLOW_API_KEY` | for detection | Roboflow **private** key — server only |
| `ROBOFLOW_MODEL_ID` | yes | `car-damage-wpmh2/1` |
| `ROBOFLOW_API_URL` | yes | `https://serverless.roboflow.com` |
| `GROQ_API_KEY` | for pricing | Groq key — server only |
| `GROQ_MODEL` | no | `openai/gpt-oss-120b` |
| `PRICING_RATE_LIMIT_PER_MINUTE` | no | Per-IP pricing limit (default 10) |
| `MAX_UPLOAD_SIZE_MB` | yes | Upload limit (default 10 MB) |
| `SUPABASE_URL` | for persistence | Project URL |
| `SUPABASE_PUBLISHABLE_KEY` | for auth | Used to verify user tokens |
| `SUPABASE_SECRET_KEY` | for persistence | Server-side key — **never** sent to clients |
| `SUPABASE_STORAGE_BUCKET` | for uploads | Default `damage-images` |
| `OVERPASS_API_URL` | for mechanics | Overpass interpreter endpoint |
| `OSRM_API_URL` | for routing | OSRM base URL |
| `MECHANIC_SEARCH_RADIUS_M` | no | Default radius (100–50 000 m) |
| `WHATSAPP_DEFAULT_COUNTRY_CODE` | no | Default dialling code (92 = PK) |

Secrets are stored as `SecretStr`; the health endpoint only reports whether a
credential is *configured*, never its value. Logs are additionally scrubbed of
known secret values.

### Database setup

Run the SQL files in `supabase/migrations/` in this order (Supabase Dashboard →
SQL Editor):
1. `20261113000100_damage_reports.sql` (table + bucket + RLS)
2. `20261113000200_auth_auto_confirm.sql` (optional auto-confirm)
3. `20261113000300_profiles.sql` (profiles + trigger)
4. `20261113000400_damage_detections.sql` (detections table, composite FK, RLS)

1. `20261113000100_damage_reports.sql` — reports table, RLS, storage bucket.
2. `20261113000300_profiles.sql` — profiles table, RLS, auto-create trigger.
3. `20261113000200_auth_auto_confirm.sql` — optional; only if you want a
   DB-level guarantee that sign-ups are auto-confirmed.

Then disable email confirmation in **Authentication → Sign In / Providers →
Email → Confirm email (off)**. Full steps and verification queries are in
[`supabase/README.md`](supabase/README.md).

---

## Running the backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- API root: <http://127.0.0.1:8000/>
- Health: <http://127.0.0.1:8000/health>
- Swagger UI: <http://127.0.0.1:8000/docs> (disabled in production)
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

---

## API endpoints

### `GET /` — service status

```json
{
  "service": "CarDamageAI API",
  "status": "ok",
  "version": "0.1.0",
  "environment": "development",
  "api_version": "v1",
  "docs_url": "/docs",
  "message": "CarDamageAI backend is running. See /docs for API documentation."
}
```

### `GET /health` — health check

Reports application liveness plus integration configuration. It does **not**
call Roboflow or Supabase (no credit/rate-limit consumption).

```json
{
  "status": "degraded",
  "service": "CarDamageAI API",
  "version": "0.1.0",
  "environment": "development",
  "timestamp": "2026-11-13T09:00:00Z",
  "checks": {
    "api": { "status": "ok", "detail": "Application is serving requests." },
    "roboflow": { "status": "not_configured", "detail": "ROBOFLOW_API_KEY is missing; damage detection is disabled." },
    "supabase": { "status": "not_configured", "detail": "SUPABASE_SECRET_KEY is missing; persistence is disabled." },
    "storage": { "status": "not_configured", "detail": "Supabase Storage is not configured." }
  }
}
```

`status` is `degraded` when an optional integration is missing, while HTTP 200
means the process itself is healthy.

### Authentication

Base path: `/api/v1/auth`. Tokens are Supabase access tokens; send them as
`Authorization: Bearer <access_token>`. The backend verifies every token with
Supabase GoTrue — it never trusts a `user_id` sent in a body.

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| POST | `/signup` | – | Create account, return session (email confirmation disabled) |
| POST | `/login` | – | Email + password → tokens |
| POST | `/refresh` | – | Refresh token → new token pair |
| POST | `/logout` | Bearer | Revoke the current session |
| GET | `/me` | Bearer | Current user + profile row |
| PATCH | `/me` | Bearer | Update `full_name` / `phone` |

**Sign up**

```http
POST /api/v1/auth/signup
Content-Type: application/json

{
  "email": "driver@example.com",
  "password": "StrongPass123",
  "full_name": "Ali Khan"
}
```

```json
{
  "user": {
    "id": "0b7c0c2e-5f4a-4a5e-9a53-7f8c2f0f6a11",
    "email": "driver@example.com",
    "phone": null,
    "role": "authenticated",
    "email_confirmed_at": "2026-11-13T10:00:00Z",
    "created_at": "2026-11-13T10:00:00Z",
    "last_sign_in_at": "2026-11-13T10:00:00Z",
    "user_metadata": { "full_name": "Ali Khan" }
  },
  "session": {
    "access_token": "<jwt>",
    "refresh_token": "<opaque>",
    "token_type": "bearer",
    "expires_in": 3600,
    "expires_at": 1763031600
  },
  "message": "Account created and signed in."
}
```

If Supabase email confirmation is enabled, `session` is `null` and the message
tells the user to confirm their email. The setup guide disables confirmation.

**Log in**

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "email": "driver@example.com", "password": "StrongPass123" }
```

Invalid credentials return `401` with a generic message (no account
enumeration) and `WWW-Authenticate: Bearer`.

**Current user**

```http
GET /api/v1/auth/me
Authorization: Bearer <access_token>
```

```json
{
  "user": { "id": "0b7c0c2e-…", "email": "driver@example.com", "…": "…" },
  "profile": {
    "id": "0b7c0c2e-…",
    "full_name": "Ali Khan",
    "phone": null,
    "created_at": "2026-11-13T10:00:00Z",
    "updated_at": "2026-11-13T10:00:00Z"
  }
}
```

**Update profile**

```http
PATCH /api/v1/auth/me
Authorization: Bearer <access_token>

{ "full_name": "Ali Khan", "phone": "+923001234567" }
```

Password policy: 8–72 characters, at least one letter and one digit. Login and
signup are rate-limited per IP (10/min and 5/min by default, configurable).
Signup, login, refresh and profile writes are all server-side; the service-role
key never leaves the backend.

### Damage detection

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| POST | `/api/v1/damage/detect` | Bearer | Upload one car photo, store it, return report metadata |
| GET | `/api/v1/damage/reports/{report_id}` | Bearer | Get a stored report with all of its detections |
| GET | `/api/v1/damage/drawbox/{report_id}` | Bearer | Get damage boxes (class, x, y, width, height) to draw on the photo |

**Requires a Supabase bearer token.** Only signed-in users can spend inference
credits; identity is verified with GoTrue before the upload is processed.

```http
POST /api/v1/damage/detect
Authorization: Bearer <access_token>
Content-Type: multipart/form-data

file=@car.jpg
```

Flow:

1. The upload is validated (type, magic bytes, decodability, size, dimensions).
2. It is stored in the public `damage-images` bucket at
   `<user_id>/<uuid>.<ext>` (service role, server-side only).
3. The object's public URL is sent to Roboflow:

   ```python
   client = InferenceHTTPClient(
       api_url="https://serverless.roboflow.com",
       api_key=os.environ["ROBOFLOW_API_KEY"],
   ).configure(
       InferenceConfiguration(
           api_key_transport="header",
           confidence_threshold=0,
           iou_threshold=0.14,
       )
   )

   result = client.infer(image_url, model_id="car-damage-wpmh2/1")
   ```

   The API uses `infer_async` (same request, non-blocking) so the event loop
   is not blocked while Roboflow runs.
4. Roboflow's response is returned plus two derived fields: the `image_url`
   used, and `damage_area_percentage` on every prediction.

Upload rules:

- JPG, JPEG, PNG or WEBP; magic bytes must match the declared content type.
- Max 10 MB (`MAX_UPLOAD_SIZE_MB`), max 50 MP (`MAX_IMAGE_PIXELS`), min 64×64.
- Animated images, corrupt files and non-images are rejected.
- Rate-limited per IP (`DAMAGE_DETECT_RATE_LIMIT_PER_MINUTE`, default 10).

```json
{
  "image_url": "https://ivdlmmkijtbcrzhiaypp.supabase.co/storage/v1/object/public/damage-images/<user_id>/<uuid>.jpg",
  "user_id": "…",
  "report_id": "…",
  "name": "car.jpg",
  "lat": null,
  "lng": null,
  "inference_id": "…",
  "time": 0.045,
  "image": { "width": 1024, "height": 768 }
}
```

Predictions are **not** returned — they are persisted to `public.damage_detections`
(x, y, width, height, class, confidence, area percentage) and can be fetched
with `GET /api/v1/damage/reports/{report_id}` or priced with
`POST /api/v1/pricing/estimate` using `report_id`.

The area formula is unchanged:

```text
damage_area_percentage = (width * height) / (image.width * image.height) * 100
```

Except for `image_url`, `user_id`, `report_id`, `name`, `lat`, `lng`, the payload
is exactly what Roboflow returned (inference metadata only). No summary,
disclaimer or generated text is added, and no detections are invented or
altered.

### Fetch a stored report

```http
GET /api/v1/damage/reports/{report_id}
Authorization: Bearer <access_token>
```

```json
{
  "id": "…",
  "user_id": "…",
  "name": "car.jpg",
  "lat": null,
  "lng": null,
  "image_url": "https://…/photo.jpg",
  "created_at": "2026-11-13T…Z",
  "updated_at": "2026-11-13T…Z",
  "detections": [
    {
      "id": "…",
      "report_id": "…",
      "user_id": "…",
      "damage_class": "dent",
      "confidence": 0.91,
      "class_id": 0,
      "detection_id": "…",
      "x": 512.0,
      "y": 384.0,
      "width": 220.0,
      "height": 160.0,
      "damage_area_percentage": 4.48,
      "created_at": "2026-11-13T…Z"
    }
  ]
}
```

Each call also stores a `damage_reports` row and one `damage_detections` row per
prediction (x, y, width, height, class, confidence, area percentage), linked by
`report_id`. `report_id` is `null` when persistence failed (for example the
migration has not been run) — the detection result is still returned. Optional
multipart fields `name`, `lat` and `lng` populate the report's label and
location; `lat` and `lng` must be sent together.

### Damage message (Roman Urdu)

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| POST | `/api/v1/message_made` | Bearer | Generate a ~150 word Roman-Urdu message about a damage |

```http
POST /api/v1/message_made
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "user_id": "…",
  "report_id": "…",
  "damage_class": "bonnet-dent",
  "lat": 33.7,
  "lng": 73.1
}
```

The backend verifies the report belongs to the caller, then uses Groq
(`openai/gpt-oss-120b`) to write a short Roman-Urdu message describing the
damage and where it is. `lat`/`lng` default to the report's stored location.

```json
{
  "user_id": "…",
  "report_id": "…",
  "damage_class": "bonnet-dent",
  "message": "…~150 words in Roman Urdu…",
  "lat": 33.7,
  "lng": 73.1,
  "language": "roman-urdu"
}
```

### Repair cost estimation

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| POST | `/api/v1/pricing/estimate` | Bearer | Price each damage in PKR and return the total |

**Requires a Supabase bearer token.** Send the damage class with how much of
the photo it covers. Compute the area from the detection box:

```text
damage_area_percentage = (box_width * box_height)
                         / (image_width * image_height) * 100
```

Or pass `report_id` (from `/damage/detect`) to price a stored report's
detections server-side.

```http
POST /api/v1/pricing/estimate
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "items": [
    { "damage_class": "front-bumper-dent", "damage_area_percentage": 20.8 },
    { "damage_class": "fender-dent", "damage_area_percentage": 2.2 }
  ]
}
```

The backend derives severity from that area (it is never left to the model):

| Damaged area | Severity | Factor |
| ------------ | -------- | ------ |
| ≤ 5% | `minor` | 0.6 |
| ≤ 15% | `moderate` | 1.0 |
| ≤ 30% | `severe` | 1.8 |
| > 30% | `critical` | 2.5 |

It then asks Groq (`GROQ_MODEL`, default `openai/gpt-oss-120b`) for a repair
price per damage in PKR. The **total is summed by the backend**, not the model:

```json
{
  "currency": "PKR",
  "provider": "groq",
  "model": "openai/gpt-oss-120b",
  "items": [
    {
      "damage_class": "front-bumper-dent",
      "damage_area_percentage": 20.8,
      "severity": "severe",
      "severity_factor": 1.8,
      "price": 27000
    },
    {
      "damage_class": "fender-dent",
      "damage_area_percentage": 2.2,
      "severity": "minor",
      "severity_factor": 0.6,
      "price": 4500
    }
  ],
  "total": 31500
}
```

Prices are AI estimates for the Pakistani market, not mechanic quotations.
Model output is validated (`1–20` items, one price per index, positive PKR
amount); malformed output becomes a `502` instead of a guessed price.

### Error format

All errors share one envelope, including the request ID for log correlation:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some submitted fields are invalid.",
    "request_id": "9f8c1e2d4b5a4c3e",
    "details": [{ "field": "body.count", "message": "Input should be a valid integer", "type": "int_parsing" }]
  }
}
```

Internal traces are never returned when `ENVIRONMENT=production`.

---

## Frontend

A modern single-page UI lives in `frontend/` (no build step — Tailwind via CDN).

```powershell
cd frontend
python -m http.server 5173
```

Open `http://127.0.0.1:5173`. The frontend calls the backend at
`http://127.0.0.1:8000` (see `API` in `frontend/index.html`).

Flows:
- Sign in / sign up.
- Detect damage: upload a photo → boxes drawn on the image, report stored.
- Draw boxes: fetch stored boxes for any report id.
- Price repair: by report id or manual items → PKR total.
- Generate a ~150 word Roman-Urdu damage message.

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest
pytest --cov=app --cov-report=term-missing
ruff check app tests
```

Tests use `Settings(_env_file=None, ...)` so a developer `.env` cannot affect
results. External services are mocked; running the suite consumes **zero**
Roboflow credits and makes no Supabase/Overpass/OSRM calls.

---

## Security notes

- `.env` is git-ignored; `.env.example` contains placeholders only.
- Roboflow and Supabase secret keys are server-side only and never serialised.
- Upload validation (magic bytes + MIME match + size + pixel count + decodability)
  runs before any inference call; only signed-in users can call detection.
- Authentication verifies Supabase-issued JWTs; client-supplied user IDs are ignored.
- Row Level Security (RLS) policies will restrict every user-owned table.
- Errors are normalised; stack traces are hidden in production.
- Security headers (`nosniff`, `DENY` framing, `no-referrer`, `no-store`) are set globally.
- Request bodies above `MAX_UPLOAD_SIZE_MB` are rejected before parsing.
- Rate limiting is configuration-gated and protects auth and detection endpoints.
- The Roboflow API key is read from settings, never logged, and errors are sanitised
  before returning to clients.

---

## Free-tier limitations

| Service | Limitation to plan for |
| ------- | ---------------------- |
| Roboflow Serverless | Monthly credit allowance; per-request latency; hard `429` when exhausted. Endpoint returns `502`/`503`, never fake detections. |
| Supabase Free | Database/storage caps, project pausing after inactivity, connection limits. RLS is mandatory. |
| Overpass API | Shared public instance, strict rate limits, occasional `429`/`504`, incomplete OSM data. |
| OSRM demo server | Best-effort, no SLA, rate-limited; used only for optional route distances. |
| WhatsApp Click-to-Chat | Free deep link only; does **not** auto-attach images and requires the user to press Send. |

---

## Roadmap

Remaining stages implement: severity heuristics, configurable PKR pricing engine,
nearby mechanics, WhatsApp links, authenticated damage reports uploads, and the
full test suite run.
