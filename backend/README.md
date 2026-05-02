# Hub Planner — Phase 2.1 (Data Layer)

This is the backend foundation. Postgres schema + data loaders. **No UI yet.**

## What's in this directory

| File | Purpose |
|---|---|
| `schema.sql` | All 5 tables + 2 views |
| `db.py` | Postgres connection helper |
| `load_pincodes.py` | Loads 19k pincode JSON → `pincodes_master` |
| `load_hierarchy.py` | Loads `combined.xlsx` → `geographic_hierarchy` |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |

## One-time setup

### 1. Create Railway project

1. Sign up at [railway.com](https://railway.com) (free tier is fine to start)
2. Click **New Project** → **Empty Project**
3. Inside the project, click **+ Create** → **Database** → **PostgreSQL**
4. Wait ~30 seconds for the Postgres service to provision

### 2. Get your connection string

1. Click the **Postgres** service in the project
2. Go to the **Connect** tab
3. Copy the **"Postgres Connection URL"** (starts with `postgresql://...`)

### 3. Set up locally

In your existing Python venv:

```bash
cd hub_planner_backend
pip install -r requirements.txt

# Copy env template and paste your Railway DATABASE_URL
cp .env.example .env
# Edit .env, paste the connection string

# Test connection
python db.py
# Should print: ✅ Database connection OK
```

### 4. Apply schema

You have two options. Easiest:

**Option A — Use Railway's built-in query interface:**
1. Open the Postgres service in Railway
2. Click the **Data** tab
3. Click **Query** → paste contents of `schema.sql` → Run

**Option B — Use psql command line (if you have it):**
```bash
psql "$DATABASE_URL" -f schema.sql
```

Verify by listing tables:
```bash
psql "$DATABASE_URL" -c "\dt"
```
Should show 5 tables.

### 5. Load pincode master data

```bash
python load_pincodes.py --input /path/to/utils-db.masterpincodes.json
```

Expected output:
```
==============================================================
  PINCODE MASTER LOAD SUMMARY
==============================================================
  total_in_file              19,xxx
  valid_rows                 19,xxx
  skipped_invalid                 0
  upserted                   19,xxx
  errors                          0
  rows_before                     0
  rows_after                 19,xxx
  net_new                    19,xxx
==============================================================
```

### 6. Load hierarchy data

```bash
python load_hierarchy.py --input /path/to/combined.xlsx
```

### 7. Sanity-check the data

In Railway's query interface (or psql), run:

```sql
-- How many pincodes per state?
SELECT state, COUNT(*) AS pincodes
FROM pincodes_master
GROUP BY state
ORDER BY pincodes DESC;

-- How many records have polygon data?
SELECT
  COUNT(*) FILTER (WHERE has_polygon) AS with_polygon,
  COUNT(*) FILTER (WHERE NOT has_polygon) AS without_polygon
FROM pincodes_master;

-- Hierarchy preview
SELECT state, district, COUNT(DISTINCT sub_district) AS subs, COUNT(*) AS villages
FROM geographic_hierarchy
GROUP BY state, district
ORDER BY state, district
LIMIT 20;
```

## When the JSON refreshes (yearly)

Just re-run `load_pincodes.py` against the new file. The `ON CONFLICT DO UPDATE`
clause means existing pincodes get updated in place; new ones get inserted.
Old pincodes that no longer exist in the source remain in the table — manually
delete them if needed.

## What's next

Phase 2.2 will add the Streamlit web app on top of these tables. You'll need
this data layer working first before the UI makes sense.
