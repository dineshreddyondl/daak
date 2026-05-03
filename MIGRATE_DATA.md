# Migrating data from local Postgres → Railway Postgres

After Railway deploys, run this **once** from your Mac to copy
the loaded data (pincodes + villages + schema) over.

## Step 1 — Get Railway DATABASE_URL

1. In Railway dashboard → click on your **Postgres** service
2. Go to the **Variables** tab
3. Copy the value of `DATABASE_PUBLIC_URL` (the public-facing one,
   not the private one that only works from inside Railway's network)
4. It will look like:
   `postgresql://postgres:abcd1234@viaduct.proxy.rlwy.net:12345/railway`

## Step 2 — Dump local DB

```bash
cd /Users/dinesh/Documents/LLM/daak

pg_dump \
  --no-owner --no-acl \
  -h localhost -p 5432 -U postgres -d daak \
  -f daak_dump.sql

# Password prompt — type: localdev
```

Verify it worked:
```bash
ls -lh daak_dump.sql        # should be ~250 MB
head -50 daak_dump.sql      # should show CREATE TABLE statements
```

## Step 3 — Restore to Railway

```bash
# Replace with YOUR DATABASE_PUBLIC_URL from step 1
RAILWAY_DB="postgresql://postgres:xxx@viaduct.proxy.rlwy.net:12345/railway"

psql "$RAILWAY_DB" < daak_dump.sql
```

This takes 5-15 minutes depending on your upload speed. You'll see
a wall of `COPY ... ` and `CREATE` statements.

## Step 4 — Verify

```bash
psql "$RAILWAY_DB" -c "SELECT COUNT(*) FROM pincodes_master;"
# expect: 19342

psql "$RAILWAY_DB" -c "SELECT COUNT(*) FROM geographic_hierarchy;"
# expect: 125559

psql "$RAILWAY_DB" -c "SELECT district, COUNT(*) FROM district_hubs GROUP BY district;"
# should list any hubs you've already created locally
```

## Step 5 — Clean up

```bash
rm daak_dump.sql        # don't accidentally commit a 250 MB SQL dump
```

## If pg_dump is not installed

```bash
brew install libpq
brew link --force libpq
```

## If you want a clean slate on Railway (no local hubs)

Add `--data-only --schema-only` carefully, or just run schema first:

```bash
# Schema only first
psql "$RAILWAY_DB" < backend/schema.sql

# Then load fresh from source files using the loader scripts
DATABASE_URL="$RAILWAY_DB" python backend/load_pincodes.py
DATABASE_URL="$RAILWAY_DB" python backend/load_hierarchy.py
```

This is slower (~30 min) but gives you a clean Railway DB
without the test hubs you've placed locally.
