-- search_schema.sql
-- DAAK Search — adds ONE new table. Existing 5 tables untouched.
-- Safe to run on both local Docker Postgres and Railway managed Postgres.
--
-- Local run:    docker exec -i daak-pg psql -U postgres -d daak < search_schema.sql
-- Railway run:  psql "$RAILWAY_DB" < search_schema.sql

-- ──────────────────────────────────────────────────────────────────────────
-- Extension: trigram similarity (for fast ILIKE '%query%')
-- ──────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ──────────────────────────────────────────────────────────────────────────
-- Table: search_index
-- One row per searchable entity. Built from pincodes_master + geographic_hierarchy.
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS search_index (
    id            BIGSERIAL PRIMARY KEY,
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('district', 'sub_district', 'village', 'pincode')),
    name          TEXT NOT NULL,         -- the searchable string (display value)
    name_lower    TEXT NOT NULL,         -- lowercased for case-insensitive search

    -- administrative path (denormalized for fast lookups)
    state         TEXT,
    district      TEXT,
    sub_district  TEXT,
    pincode       TEXT,

    -- geo (only populated for pincode rows; null otherwise)
    centroid_lat  DOUBLE PRECISION,
    centroid_lng  DOUBLE PRECISION,

    -- room to grow without schema changes
    metadata      JSONB DEFAULT '{}'::jsonb,

    indexed_at    TIMESTAMPTZ DEFAULT now()
);

-- ──────────────────────────────────────────────────────────────────────────
-- Indexes
-- ──────────────────────────────────────────────────────────────────────────

-- Trigram index on name_lower → fast ILIKE / similarity search
CREATE INDEX IF NOT EXISTS idx_search_name_trgm
    ON search_index USING gin (name_lower gin_trgm_ops);

-- Filter by type (used when user selects "districts only" etc.)
CREATE INDEX IF NOT EXISTS idx_search_type
    ON search_index (entity_type);

-- Pincode lookups (digits only, no fuzzy needed)
CREATE INDEX IF NOT EXISTS idx_search_pincode
    ON search_index (pincode)
    WHERE entity_type = 'pincode';

-- For drilling into a district's sub-districts/villages
CREATE INDEX IF NOT EXISTS idx_search_district
    ON search_index (state, district)
    WHERE district IS NOT NULL;

-- ──────────────────────────────────────────────────────────────────────────
-- Sanity check
-- ──────────────────────────────────────────────────────────────────────────
\d search_index