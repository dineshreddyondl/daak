# Hub Planner — Local Setup with Docker

Goal: get Postgres running on your Mac, schema applied, real data loaded,
verified with queries — all without touching Railway yet.

## Prerequisites

1. **Docker Desktop installed** ([download](https://www.docker.com/products/docker-desktop))
2. **Your existing Python venv** (from the village/pincode work)
3. **Two data files** ready:
   - Your full 19k pincode JSON (e.g. `utils-db.masterpincodes.json`)
   - Your `combined.xlsx` from `collate_villages.py`

## Step 1 — Start Postgres in Docker (one-time)

Open Terminal and run:

```bash
docker run -d \
  --name hub-planner-pg \
  -e POSTGRES_PASSWORD=localdev \
  -e POSTGRES_DB=hubplanner \
  -p 5432:5432 \
  postgres:16
```

**What this does:**
- `-d` runs in the background
- `--name hub-planner-pg` so we can refer to it later
- `POSTGRES_PASSWORD=localdev` — local-only password, fine to be simple
- `POSTGRES_DB=hubplanner` — database name
- `-p 5432:5432` — exposes Postgres on the standard port
- `postgres:16` — the official Postgres image, version 16

You'll see a long ID printed. That's the container ID. Verify it's running:

```bash
docker ps
```

You should see `hub-planner-pg` listed with `STATUS` as "Up X seconds".

**Common gotcha:** if port 5432 is already in use (e.g., you have local Postgres
installed via Homebrew), you'll get a port conflict. Either stop the other
Postgres or use a different port like `-p 5433:5432`. If you change it,
update the connection string below to use port 5433.

## Step 2 — Set up the Python environment

Inside the `hub_planner_backend` folder:

```bash
cd /Users/dinesh/Documents/LLM/python/hub_planner_backend
# (adjust path if you put it elsewhere)

# Activate your existing venv
source ../.venv/bin/activate

# Install the new dependencies
pip install -r requirements.txt
```

## Step 3 — Configure connection

Copy the env template and edit it:

```bash
cp .env.example .env
```

Open `.env` in any editor and replace the `DATABASE_URL` line with:

```
DATABASE_URL=postgresql://postgres:localdev@localhost:5432/hubplanner
GOOGLE_MAPS_API_KEY=your_key_here
```

(Google Maps key isn't needed for the loaders — leave it as-is for now.)

## Step 4 — Test the connection

```bash
python db.py
```

You should see:
```
✅ Database connection OK
```

If you get a connection error:
- Is Docker container running? `docker ps` should show it
- Is port 5432 accessible? `lsof -i :5432` should show postgres listening
- Did you save .env? Common mistake.

## Step 5 — Apply the schema

The cleanest way is via `psql` inside the Docker container itself:

```bash
docker exec -i hub-planner-pg psql -U postgres -d hubplanner < schema.sql
```

You should see a series of `CREATE TABLE` and `CREATE INDEX` and `CREATE VIEW`
messages — about 15 of them.

Verify:

```bash
docker exec -it hub-planner-pg psql -U postgres -d hubplanner -c "\dt"
```

You should see 5 tables:
- `district_coverage`
- `district_hubs`
- `district_status`
- `geographic_hierarchy`
- `pincodes_master`

## Step 6 — Load the pincode master data

```bash
python load_pincodes.py --input /path/to/utils-db.masterpincodes.json
```

Expected output (numbers will vary based on your file):

```
2026-05-02 [INFO] Reading utils-db.masterpincodes.json …
2026-05-02 [INFO] Found 19234 raw records
2026-05-02 [INFO] Parsed 19200 valid rows; skipped 34 invalid
2026-05-02 [INFO]   upserted batch 1 / 20 (1000 rows)
... (more batches)
============================================================
  PINCODE MASTER LOAD SUMMARY
============================================================
  total_in_file              19,234
  valid_rows                 19,200
  skipped_invalid                34
  upserted                   19,200
  errors                          0
  rows_before                     0
  rows_after                 19,200
  net_new                    19,200
============================================================
```

If anything errors out, paste the output and we'll fix it.

## Step 7 — Load the hierarchy data

```bash
python load_hierarchy.py --input /path/to/combined.xlsx
```

Expected output:

```
============================================================
  HIERARCHY LOAD SUMMARY
============================================================
  rows_loaded         XX,XXX
  districts              XXX
  states                   7
  flagged              X,XXX
============================================================
```

## Step 8 — Sanity-check queries

Get a Postgres shell:

```bash
docker exec -it hub-planner-pg psql -U postgres -d hubplanner
```

You're now inside Postgres. Run these queries one by one:

**Query 1: pincodes per state**

```sql
SELECT state, COUNT(*) AS pincodes
FROM pincodes_master
GROUP BY state
ORDER BY pincodes DESC;
```

You should see ~36 states/UTs. Sanity check: India has ~19k pincodes total,
distributed roughly proportional to population (UP, Maharashtra, AP among the largest).

**Query 2: polygon coverage**

```sql
SELECT
  COUNT(*) FILTER (WHERE has_polygon) AS with_polygon,
  COUNT(*) FILTER (WHERE NOT has_polygon) AS without_polygon,
  ROUND(100.0 * COUNT(*) FILTER (WHERE has_polygon) / COUNT(*), 1) AS pct_with_polygon
FROM pincodes_master;
```

This tells you what fraction of pincodes have rich polygon data vs only
bounding-box. From the Vizag sample, 56/61 = ~92% had polygons; the rest of
your data should be similar.

**Query 3: do district names match between the two tables?**

This is the most important sanity check — if district names don't match
between `pincodes_master` and `geographic_hierarchy`, joins will fail
silently later.

```sql
-- Districts in pincodes_master but NOT in hierarchy:
SELECT DISTINCT pm.district, pm.state
FROM pincodes_master pm
LEFT JOIN geographic_hierarchy gh
  ON LOWER(pm.district) = LOWER(gh.district)
WHERE gh.district IS NULL
  AND pm.district IS NOT NULL
ORDER BY pm.state, pm.district
LIMIT 30;
```

Empty result = good. Any rows = name mismatches we need to handle later
(spelling differences, etc.).

**Query 4: Vizag sanity check**

```sql
-- How many Vizag pincodes did we load?
SELECT COUNT(*) FROM pincodes_master WHERE LOWER(district) LIKE '%visakhapa%';
```

Should be around 60-100 depending on your data.

**Query 5: hierarchy sample**

```sql
SELECT state, district, COUNT(DISTINCT sub_district) AS sub_districts, COUNT(*) AS villages
FROM geographic_hierarchy
GROUP BY state, district
ORDER BY state, district
LIMIT 15;
```

Exit psql with `\q`.

## Step 9 — Save your sanity-check results

Take screenshots or copy-paste outputs of the queries above. Share them with me
and I'll review for any data quality issues before we build the app on top.

## Daily workflow (after initial setup)

**Start of day:**
```bash
docker start hub-planner-pg
```

**End of day (optional):**
```bash
docker stop hub-planner-pg
```
(Data is preserved between starts/stops.)

**Want to wipe everything and start fresh?**
```bash
docker rm -f hub-planner-pg
# then re-run the docker run command from Step 1
```

## When local works → moving to Railway

Once everything works locally, deploying to Railway is just:

1. Add Postgres plugin on Railway
2. Apply schema there (paste schema.sql in their Data → Query tab)
3. Run `load_pincodes.py` again, this time with `DATABASE_URL` pointing at Railway

Same scripts, different `DATABASE_URL`. The whole point of using Docker locally
was to make this transition painless.

## Common issues & fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `Connection refused` on port 5432 | Docker container not running | `docker start hub-planner-pg` |
| `password authentication failed` | Wrong password in .env | Should be `localdev` (matches Step 1) |
| `database "hubplanner" does not exist` | Container created without DB env var | Re-run docker command from Step 1 |
| Schema apply: `relation already exists` | Schema partially applied | Either ignore (CREATE IF NOT EXISTS) or drop + recreate |
| Loader hangs forever | psycopg2 timeout | Check `docker logs hub-planner-pg` for errors |
