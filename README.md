# DentSense — Car Damage AI

Upload a car photo → AI detects the damage (bounding boxes) → per-damage **PKR** repair
estimate → nearest mechanics (within 10 km) → auto-written **Roman-Urdu** WhatsApp message
that you can send to a mechanic with the photo + location.

Designed and developed by **Muhammad Hamza**.

## Stack

| Layer    | Tech |
|----------|------|
| Backend  | Python 3.11, FastAPI, Uvicorn, Pydantic v2 |
| Database | Supabase (Postgres + RLS + Storage bucket `damage-images`) |
| Vision   | Roboflow `inference-sdk` — model `car-damage-wpmh2/1` |
| AI text  | Groq `openai/gpt-oss-120b` (pricing + Roman-Urdu message) |
| Maps     | Geoapify Places (primary) → Nominatim/Overpass (fallback) |
| Frontend | Single-page HTML + Tailwind CDN + Canvas (no build step) |

## Run — backend

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Copy `backend/.env.example` → `backend/.env` and fill the keys
(Supabase, Roboflow, Groq, Geoapify). `.env` is git-ignored — never commit keys.

## Run — frontend

```powershell
cd frontend
python serve.py          # static server on port 5173 (no-cache)
```

Open `http://127.0.0.1:5173`.

### Backend base URL

`frontend/config.js` holds the backend URL — change it there, no need to touch `index.html`:

```js
window.DS_CONFIG = { API_BASE_URL: '' };   // '' = auto (same host, port 8000)
```

Or override per-visit with a query param (highest priority):

```
http://192.168.100.121:5173/?api=http://192.168.100.121:8000
```

### Test from your phone (same Wi-Fi)

Frontend and backend bind to `0.0.0.0`, and dev CORS allows LAN origins.
On the phone open: `http://<PC-LAN-IP>:5173` (e.g. `http://192.168.100.121:5173`).
Allow Python through the Windows Firewall (Private network) if it does not load.

## Database migrations

Run the SQL files in `backend/supabase/migrations/` in order, via
**Supabase Dashboard → SQL Editor**.

## Features

- Auth-only detection (Supabase bearer tokens, silent refresh, no client-supplied `user_id`)
- Upload / camera capture / drag-and-drop, auto browser geolocation
- Detection boxes drawn at natural image resolution (confidence 0, IoU 0.14, 85% opacity)
- Unique damages with per-item prices + **TOTAL** in PKR
- "No Damage Detected" state when the model returns `predictions: []`
- Car-company dropdown sent with the report and included in the AI message
- Selectable nearby mechanics → message rewritten with that mechanic's name
- Green WhatsApp button → `wa.me` with image URL + Google Maps location + message

## Disclaimer

AI estimates only — not a verified inspection or a mechanic quotation.
