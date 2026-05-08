# DAAK — Changelog

A running log of significant changes to the DAAK product.

Format: `## YYYY-MM-DD` heading per day, bullet list under each. Newest first.

---

## 2026-05-04

- **UI rebrand** — Replaced 🚚 truck emoji with ONDL logo image. "daak" → "DAAK". Version badge "v0.3" → "Beta".
- **UI clarity** — Mode toggle relabeled: `🔍 Search` / `📍 Pick by area` → `📍 Choose Manually (Sub-district)` (left, default) / `🔍 Use Google Maps` (right). Section heading "4. Hub Details" → "4. Virtual Hub Details".
- **Docs** — Added `HANDOFF.md` and `CHANGELOG.md` for context preservation.

## 2026-05-03

- **Production deploy** on Railway — separate `daak-api` and `daak-web` services + managed Postgres. Cost ~$10-15/mo (Hobby plan).
- **Custom domain** — `daak.odlnetwork.com` configured via Cloudflare DNS only (grey cloud). Vite preview's `allowedHosts: true` to accept any host.
- **Data migrated** to Railway Postgres via `pg_dump | psql` — 19,342 pincodes + 125,559 villages + 4 hubs from local dev.
- **Production code patches:**
  - `api/main.py` — read `CORS_ORIGINS` from env (comma-separated allowlist), use `$PORT` env var
  - `api/Procfile` — Railway start command
  - `web/vite.config.js` — `preview.allowedHosts: true` for any domain
  - `web/src/App.jsx` — all `fetch()` calls + Excel download URLs prefixed with `${VITE_API_URL}`
- **Security fix** — Real Google Maps key was committed to root `.env.example`. Rotated the key in Cloud Console immediately. Sanitized the file with placeholder. New key restricted by HTTP referrer to localhost + Railway + custom domain.
- **Built Serviceability page** — list view (table + filters + bulk download), read-only detail view, multi-tab Excel exports for DRAFT and FINALIZED districts. Header tabs (Builder | Serviceability) replace single-page layout.
- **Builder cleanup** — Removed "Export this district" button (Serviceability is now the single download surface). Added auto-save indicator (pulsing green dot). Added "Save as Draft" button (cosmetic confirmation only — data auto-saves on every action).
- **Click-to-toggle exclude** — Click any covered (green) polygon → InfoWindow with "Exclude from coverage" / "Re-include" button. Soft-delete only (`is_excluded` flag). Excluded polygons render grey with red border + ✗ in label.
- **Pincode labels** on covered polygons — auto-hide at zoom < 11.
- **Search restricted to district** — Google Places autocomplete uses `strictBounds: true` against district bounding box. Auto re-binds when district changes.

## 2026-05-02

- **Pivoted from Streamlit to FastAPI + React** — Streamlit could not match the v4 HTML proto's pixel-perfect layout. Rebuilt frontend in React + Vite.
- **Built Builder view** — state/district picker, hub placement (search mode + area mode), live preview circle, polygon rendering (green=covered, blue=uncovered).
- **Backend API** — FastAPI with haversine math, multi-tab Excel exports, `district_status` workflow (DRAFT → FINALIZED).

## 2026-05-01

- **Data layer complete** — Postgres in Docker (`daak-pg`), 5 tables + 2 views, 19,342 pincodes loaded (322 skipped due to missing centroids), 125,559 villages across 7 states.
- **District name normalizations applied** — 17 SQL UPDATE statements to fix mismatches between source data spellings (e.g., `Visakhapatanam` → `Visakhapatnam`).

## Earlier sessions

- HTML proto v1 → v4 (sidebar + map layout exploration)
- Source data prep — `collate_villages.py`, `clean_pincodes.py`, district centroid computation (later abandoned as wrong abstraction)

---

## How to update this file

When you ship something:

1. Add a new `## YYYY-MM-DD` heading at the top if today's date isn't there yet
2. Add bullet points under it describing what shipped
3. Commit the change alongside the feature: `git commit -m "feat: X" -m "CHANGELOG.md updated"`

Aim for one bullet per shipped change. Group related small changes. Don't list internal refactors that have no user-visible effect.

## 2026-05-04

- **UI rebrand** — Replaced 🚚 truck emoji with ONDL logo image. "daak" → "DAAK". Version badge "v0.3" → "Beta".
- **UI clarity** — Mode toggle relabeled: ...
- **Docs** — Added `HANDOFF.md` and `CHANGELOG.md` for context preservation.
- **Search tab** — Third tab added: single search across districts, sub-districts, villages, and pincodes; mixed-type results with type filter pills; debounced live search; metadata panel + Google Map preview for pincodes; drilldown showing children (sub-districts, villages, pincodes) with click-through navigation; "Open in Builder" action that pre-fills state/district/sub-district/village.
- **Bulk Lookup** — Paste up to 500 names, get best match + alternatives + confidence per row. NA rows for no-match. XLSX export with Results + Alternatives sheets. Auto-detect by type (numeric → pincode) or manual override.
- **New `search_index` table** — Populated from existing `pincodes_master` + `geographic_hierarchy`. 146,351 rows: 192 districts, 2335 sub-districts, 124,482 villages, 19,342 pincodes. `pg_trgm` extension for fast fuzzy matches. Existing 5 tables untouched.
- **Theme toggle** — Sun/moon button replaces "admin" in top-right. Light/dark via CSS variables. Persists to localStorage with OS preference fallback.
- **Logo/title clickable** — Brand area in header returns to Builder.
- **Builder prefill extended** — Now also accepts `sub_district` and `village` (previously only state + district).
- **API additions:** `GET /api/search`, `POST /api/search/bulk`, `POST /api/search/bulk/export`. `GET /api/villages` extended to accept district-only (omitting sub_district).
- **One-time scripts:** `backend/search_schema.sql`, `backend/load_search_index.py`.

## 2026-05-03

## 2026-05-08 — Lane Identifier

- **New `Lanes` tab** — fourth tab. Pick an origin district from a custom combo dropdown (search inside the dropdown with match highlighting). See all destinations within a min/max distance band (default 150–800 km). Useful for shortlisting lanes when planning hubs.
- **New `district_centroids` table** — one row per (state, district), populated once via Google Geocoding for 780 districts (UNION of `pincodes_master` + `geographic_hierarchy`). Stores lat/lng + bounding box + formatted address + place_id.
- **Lane query response** — flat sorted list with per-district `sub_district_count` and `village_count` from a single `GROUP BY` on `geographic_hierarchy` (no N+1).
- **Lanes UI** — filter-as-you-type across results, expandable rows showing sub-districts and villages (lazy loaded), optional map view with origin pin and destination dots, XLSX download with min/max in title row and as a column.
- **Builder enhancement** — purple District HQ star marker on the Builder map (uses the centroid we already geocoded). Hover for full address. Visual reference only — does not pre-fill hub coordinates.
- **API additions:** `GET /api/lanes/origins`, `GET /api/lanes/from-district?state=&district=&min_km=&max_km=`, `GET /api/lanes/from-district/export` (XLSX), `GET /api/district_centroid?state=&district=`.
- **One-time scripts:** `backend/district_centroids_schema.sql`, `backend/load_district_centroids.py`.
- **No master data touched.** `pincodes_master`, `geographic_hierarchy`, `search_index` all untouched. Lane Identifier is purely additive.
