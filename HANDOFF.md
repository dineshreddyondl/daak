# DAAK — Handoff Document

> If you're an AI assistant or developer picking up this project mid-stream, read this first. It will save you (and the user) hours.

Last updated: 2026-05-04

---

## Product overview

**DAAK** is an internal tool that lets the operations team at ONDL define which pincodes are serviceable in each Indian district. The Hindi word *daak* means "post / mail".

### The problem it solves

Ops used to manually collate per-district pincode spreadsheets to share with enterprise clients. Painful, error-prone, slow. DAAK replaces that with a map-driven workflow:

1. Ops picks a district (e.g. Vijayawada)
2. Places one or more "virtual hubs" on the map — typically physical locations like railway stations, depots, etc.
3. Each hub has a configurable radius (default 25 km)
4. The system uses haversine distance to find every pincode whose centroid falls inside any hub's radius
5. Ops can manually exclude individual pincodes by clicking polygons on the map (e.g. "we technically reach this area but our riders can't deliver there")
6. When done, ops "finalizes" the district — locking it from edits
7. Excel exports go to enterprise clients

The "virtual hub" concept is a planning abstraction — there isn't always a real hub at that lat/lng. It's just the centroid of a serviceable region.

### Who uses it

- ONDL operations team
- Currently no auth — public URL, but the URL is shared verbally
- Eventually will have Cognito-based login

---

## Tech stack

| Layer | Tech | Why |
|---|---|---|
| Database | PostgreSQL 16 (Railway managed) | Indian geo polygons stored as GeoJSON in JSONB |
| Backend | FastAPI 0.115 (Python 3.12) | Simple, fast, type hints |
| Frontend | React 18 + Vite + Google Maps JS API | Map-heavy UI, hot-reload friendly |
| Hosting | Railway (Hobby plan, ~$10-15/mo) | Auto-deploy from GitHub, managed Postgres |
| Domain | `daak.odlnetwork.com` via Cloudflare DNS | Cloudflare is **DNS only** (grey cloud) — Railway handles SSL |
| Repo | https://github.com/dineshreddyondl/daak | Single repo, monorepo-style with `api/` and `web/` |

### Tech NOT used (and why)

- **Streamlit**: Tried first, abandoned. Cannot match the pixel-perfect sidebar+map layout the team wanted. Wasted ~2 hours. Don't go back to this.
- **Docker**: Considered but skipped. Nixpacks (Railway's auto-build) handles FastAPI + Vite cleanly. Could be added later if portability becomes a concern.
- **AWS Fargate**: Compared to Railway — costs ~$40-80/mo vs $10-15. Not worth the move at current scale.

---

## Repository structure

```
daak/
├── .env.example              # placeholder values only — never put real keys here
├── .gitignore
├── README.md
├── HANDOFF.md                # this file
├── CHANGELOG.md              # log of changes
├── MIGRATE_DATA.md           # how to dump-and-restore Postgres to Railway
├── api/                      # FastAPI backend
│   ├── main.py               # all 20+ endpoints in one file
│   ├── requirements.txt
│   ├── Procfile              # tells Railway how to start (uvicorn ...)
│   ├── .env.example
│   └── .env                  # gitignored — DATABASE_URL, etc.
├── backend/                  # one-time data load + schema scripts (NOT deployed)
│   ├── schema.sql
│   ├── load_pincodes.py
│   ├── load_hierarchy.py
│   └── db.py
├── data/                     # gitignored, large source files
│   ├── masterpincode.db.json # 236 MB — pincode geometries
│   └── districtdata.xlsx     # 5.5 MB — village hierarchy
└── web/                      # React + Vite frontend
    ├── package.json
    ├── vite.config.js        # Vite preview must allow Railway/custom hosts
    ├── index.html
    ├── .env.example
    ├── .env                  # gitignored — VITE_GOOGLE_MAPS_API_KEY, VITE_API_URL
    ├── public/
    │   └── ondl-logo.png     # header brand
    └── src/
        ├── main.jsx          # mounts React, loads Google Maps script
        ├── App.jsx           # ALL components inline — Builder, Serviceability, etc.
        └── styles.css        # ~290 lines, single stylesheet
```

### A note on monolithic files

`App.jsx` is ~1000 lines with three components inline (Root, BuilderView, ServiceabilityView). This is intentional for quick iteration during the build phase. If you're adding significant features, split into separate files — but understand that the user's velocity has been built around fast single-file edits.

---

## Database

### Connection (Railway)

- **DATABASE_URL** — internal Railway URL, used by the API service
- **DATABASE_PUBLIC_URL** — external URL, used from your Mac for `psql`/`pg_dump`

The `daak-api` service has a `DATABASE_URL` variable set as a **reference** to `${{Postgres.DATABASE_URL}}` — DO NOT replace with a literal URL.

### Schema (5 tables, 2 views)

| Table | Rows | Purpose |
|---|---|---|
| `pincodes_master` | 19,342 | Pincode polygons + centroids. Primary geo data. |
| `geographic_hierarchy` | 125,559 | State / district / sub-district / village hierarchy |
| `district_status` | varies | Tracks DRAFT / FINALIZED state per district |
| `district_hubs` | varies | Hubs placed by ops |
| `district_coverage` | varies | Per-pincode coverage row, with `is_excluded` flag for soft delete |

**Views:**
- `v_district_active_pincodes` — coverage rows with `is_excluded = false`
- `v_district_full_audit` — joined view for export

### Data quirks (IMPORTANT)

1. **322 pincodes were skipped at load time** because they had no centroid in source JSON. They are invisible to the system. Decision: accepted this gap for now.
2. **District name normalizations applied via SQL UPDATEs.** Source data spelled districts inconsistently (e.g., `Visakhapatanam` vs `Visakhapatnam`, `Kanchipuram` vs `Kancheepuram`). The full list of 17 normalizations is in the `2026-05-02-...` transcript. After normalization, 6 acceptable strays remain (115 pincodes, 0.6% loss).
3. **Pincodes without polygons (1,314)** still have centroids → they appear as small dots on the map and are fully included in coverage calculations.

### Schema file

`backend/schema.sql` is the source of truth. If you ever recreate the database, run this first.

---

## Deployment (Railway)

### Project layout

Three services in one Railway project:

1. **Postgres** — managed by Railway
2. **daak-api** — root dir `api`, custom start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. **daak-web** — root dir `web`, build: `npm install && npm run build`, start: `npx vite preview --host 0.0.0.0 --port $PORT`

### Environment variables

**daak-api:**
- `DATABASE_URL` — reference to `${{Postgres.DATABASE_URL}}`
- `CORS_ORIGINS` — comma-separated allowlist. Currently:
  - `https://daak-web-production.up.railway.app,https://daak.odlnetwork.com`
  - **Watch for trailing slashes — they break CORS matching.**

**daak-web:**
- `VITE_GOOGLE_MAPS_API_KEY` — Google Maps JS API key (restricted by HTTP referrer)
- `VITE_API_URL` — `https://daak-production.up.railway.app` (the API public URL)

### Custom domain

- `daak.odlnetwork.com` configured in Cloudflare as CNAME → `daak-web-production.up.railway.app`
- **Cloudflare proxy: DNS only (grey cloud)**. If you turn on the orange Proxied cloud, you must set Cloudflare SSL/TLS to "Full (strict)" or things break.
- Vite preview's `allowedHosts: true` in `vite.config.js` so it accepts both Railway and custom domain.

### Deploy flow

1. Edit code locally
2. Test on `http://localhost:5173`
3. `git push` → Railway auto-detects and rebuilds
4. ~3 min build → live at `https://daak.odlnetwork.com`
5. Hard refresh browser to bust Vite caches: `Cmd+Shift+R`

### Rollback

Railway dashboard → daak-web → Deployments → find a previous successful deploy → ⋮ → "Redeploy this version".

---

## Google Maps API

### Setup

- Single key, restricted by HTTP referrer
- Allowed referrers (set in Google Cloud Console):
  - `http://localhost:5173/*` (local dev)
  - `https://daak-web-production.up.railway.app/*`
  - `https://daak.odlnetwork.com/*`
- API restrictions: Maps JavaScript API + Places API + Geocoding API only

### Critical history

The original key `AIzaSyDVB6BL8m9NjQ...` was accidentally committed to git in `.env.example` at the repo root. **It was rotated.** A new key is in use. The dead key still sits in commit `cd55833` in git history — harmless but cosmetic. Could be scrubbed via `git filter-repo` if desired.

### Cost

Light internal use → ~$2-5/mo. Pay-as-you-go.

---

## What's currently built

### Builder view (left tab)

- State → district cascading dropdowns
- District stats card (pincode count, sub-districts, villages)
- **Two modes for hub placement:**
  - "🔍 Use Google Maps" → Places autocomplete restricted to district bounding box (`strictBounds: true`)
  - "📍 Choose Manually (Sub-district)" → drill down via sub-district + village dropdowns, geocode address → auto-fill lat/lng
- Click anywhere on map → use that as hub location
- Live red preview circle as user adjusts radius
- "Add Hub & Find Pincodes" → backend computes haversine, inserts coverage rows
- Pincode polygons rendered:
  - Green = covered
  - Blue = uncovered (within district but outside any hub radius)
  - Grey with red border = excluded
- Pincode label (6-digit) on each covered polygon (auto-hides at zoom < 11)
- **Click a green polygon → InfoWindow with toggle**: "Exclude from coverage" or "Re-include"
- Hub list in sidebar with delete button (DRAFT only)
- Live coverage summary: active rows, unique pincodes, excluded count
- Auto-saved indicator (pulsing green dot)
- "Save as Draft" button (cosmetic — data is auto-saved on every action)
- "Finalize district" button (locks it, no more edits)

### Serviceability view (right tab)

- Top-level metrics: districts, total hubs, active rows, excluded
- Filter bar: state, status, search by district name (300ms debounce)
- Table: state | district | status pill | hubs | pincodes(excluded) | last updated | actions
- Click district name or "View" → detail page
- Bulk download buttons:
  - "📥 Download all DRAFT districts" — multi-tab Excel
  - "📥 Download all FINALIZED districts" — multi-tab Excel

### Detail view (per district)

- Hub list (left)
- Pincode coverage table with active/excluded pills (right, sticky header, scrollable)
- "Continue editing in Builder" (DRAFT only) — pre-fills Builder
- "Download this district" → single-district Excel
- Read-only if FINALIZED

### Excel format

Multi-tab workbook:
- **Overview** tab: list of districts with summary
- **One tab per district**: header, status, hubs section, pincode coverage section (with status: ACTIVE / EXCLUDED + reason)
- Sheet names sanitized (≤31 chars, no special chars)

---

## Backend endpoints

All routes defined in `api/main.py`. Loose categorization:

**Read (Builder):**
- `GET /api/health` — DB ping
- `GET /api/states`, `/api/districts`, `/api/sub_districts`, `/api/villages` — dropdowns
- `GET /api/district/stats` — counts for selected district
- `GET /api/district/pincodes` — pincode geometry
- `GET /api/district/hubs` — hubs + coverage rows for district

**Mutations:**
- `POST /api/district/{state}/{district}/hubs` — create hub, run haversine
- `DELETE /api/hub/{hub_id}` — delete hub (DRAFT only)
- `POST /api/coverage/{coverage_id}/toggle` — flip `is_excluded` (DRAFT only, soft only)
- `POST /api/coverage/{coverage_id}/exclude` — legacy alias for toggle
- `POST /api/district/{state}/{district}/finalize` — lock district to FINALIZED

**Serviceability:**
- `GET /api/serviceability/overview` — list districts with counts (filterable)
- `GET /api/serviceability/summary` — top-line metrics
- `GET /api/serviceability/district` — read-only detail for one district

**Exports:**
- `GET /api/export/district?district=X` — single-district Excel
- `GET /api/export/drafts` — multi-tab Excel of all DRAFTs
- `GET /api/export/finalized` — multi-tab Excel of all FINALIZED
- `GET /api/export/all` — alias for /finalized (backwards compat)

---

## Common workflows

### Run locally

```bash
# Two terminals.

# Terminal 1: API
cd api
source .venv/bin/activate
uvicorn main:app --reload --port 8000

# Terminal 2: Web
cd web
npm run dev    # opens http://localhost:5173
```

Postgres runs in Docker locally:
```bash
docker start daak-pg    # if it's stopped
```

### Push to production

```bash
cd /Users/dinesh/Documents/LLM/daak
git add -A
git commit -m "..."
git push
# Railway auto-deploys both api and web
```

### Migrate fresh data to Railway

See `MIGRATE_DATA.md`. TL;DR:
```bash
read -s RAILWAY_DB    # paste DATABASE_PUBLIC_URL
pg_dump --no-owner --no-acl -h localhost -p 5432 -U postgres -d daak -f daak_dump.sql
psql "$RAILWAY_DB" < daak_dump.sql
```

---

## What's deferred (NOT built yet)

Listed in priority order:

1. **Authentication** — currently no login. Plan: AWS Cognito user pool. URL is shared verbally for now.
2. **The "additional layer"** — user has a new feature in mind (described separately, not yet specified)
3. **322 missing-centroid pincodes** — not loaded into DB. Could be backfilled manually or via approximate district centroids. Decision: ignore unless ops complains.
4. **Git history scrub** — leaked Maps key still in commit `cd55833`. Cosmetic only since key is rotated.
5. **Custom domain for API** — currently `daak-production.up.railway.app`. Could be `api.daak.odlnetwork.com`.

---

## Known quirks / gotchas

1. **Multi-line `cat` heredocs sometimes fail silently** when pasted in chat. Verify with `wc -l` after creating files.
2. **`vite.config.js` `allowedHosts`** must include any new domain you use. Currently set to `true` (allow all).
3. **Two-build deploys are normal** — when you push code touching both `api/` and `web/`, both services rebuild in parallel.
4. **`.env.example` files** must contain ONLY placeholders. Never copy your real `.env` content over by accident.
5. **CORS strings need exact match** — no trailing slashes. `https://daak.odlnetwork.com/` will fail; `https://daak.odlnetwork.com` works.
6. **District name case-sensitivity** — backend normalizes to lowercase via `LOWER()`. Frontend passes mixed-case names; this is fine.
7. **Cloudflare proxy mode** — keep grey cloud (DNS only) unless you've configured Cloudflare SSL/TLS to "Full (strict)".

---

## How the user (dinesh) likes to work

- Mac, Hyderabad timezone (IST)
- Prefers small incremental changes that get tested immediately, not big refactors
- Wants to see HTML protos before React patches for any UI changes ("show me first")
- Says "next" or "deployed" when ready to move to the next step
- Will paste screenshots when something looks wrong
- Doesn't want long preambles — get to the action
- Keeps `.env.example` files updated when adding new env vars

---

## If you're an AI assistant resuming this project

Read this whole file. Then:

1. Confirm the user's last action by checking recent git log: `cd /Users/dinesh/Documents/LLM/daak && git log --oneline -20`
2. Confirm what's in production by visiting `https://daak.odlnetwork.com`
3. Ask the user "what are we building today?" rather than assuming
4. When in doubt, **ask before patching** — the user prefers a 30-second clarification over 5 minutes of wrong code

The user has built this from scratch in ~3 sessions. They know the codebase. Don't condescend.
