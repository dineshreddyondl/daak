-- district_centroids_schema.sql
-- DAAK Lane Identifier — adds ONE new table. Existing tables untouched.
-- Run on both local Docker Postgres and Railway managed Postgres.
--
-- Local run:    docker exec -i daak-pg psql -U postgres -d daak < district_centroids_schema.sql
-- Railway run:  psql "$RAILWAY_DB" < district_centroids_schema.sql

-- ──────────────────────────────────────────────────────────────────────────
-- Table: district_centroids
-- One row per (state, district). Populated by load_district_centroids.py.
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS district_centroids (
    state             TEXT NOT NULL,
    district          TEXT NOT NULL,

    -- Center point (Google's geometry.location)
    lat               DOUBLE PRECISION NOT NULL,
    lng               DOUBLE PRECISION NOT NULL,

    -- Bounding box (Google's geometry.viewport — always present)
    bbox_ne_lat       DOUBLE PRECISION,
    bbox_ne_lng       DOUBLE PRECISION,
    bbox_sw_lat       DOUBLE PRECISION,
    bbox_sw_lng       DOUBLE PRECISION,

    -- Audit fields
    formatted_address TEXT,            -- what Google echoed back ("Visakhapatnam, Andhra Pradesh, India")
    place_id          TEXT,            -- Google's stable identifier
    source            TEXT NOT NULL DEFAULT 'google_geocoding',
    geocoded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (state, district)
);

-- Quick lookup by district name only (for cross-state matches if needed)
CREATE INDEX IF NOT EXISTS idx_district_centroids_district
    ON district_centroids (district);

\d district_centroids
