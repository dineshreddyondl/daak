-- ============================================================================
-- Hub Planner — PostgreSQL Schema
-- ----------------------------------------------------------------------------
-- Run once on Railway Postgres after creating the database:
--   psql $DATABASE_URL -f schema.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. PINCODES MASTER  (read-only ground truth from JSON, refreshed yearly)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pincodes_master (
    pincode             VARCHAR(10) PRIMARY KEY,
    city                VARCHAR(255),
    district            VARCHAR(255),
    state               VARCHAR(255),
    country             VARCHAR(64) DEFAULT 'India',
    centroid_lat        DOUBLE PRECISION,
    centroid_lng        DOUBLE PRECISION,
    bbox_ne_lat         DOUBLE PRECISION,
    bbox_ne_lng         DOUBLE PRECISION,
    bbox_sw_lat         DOUBLE PRECISION,
    bbox_sw_lng         DOUBLE PRECISION,
    boundary_geojson    JSONB,                        -- full polygon if available
    has_polygon         BOOLEAN DEFAULT FALSE,
    source_updated_at   TIMESTAMP,                    -- updatedAt from source JSON
    loaded_at           TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pincodes_district  ON pincodes_master (LOWER(district));
CREATE INDEX IF NOT EXISTS idx_pincodes_state     ON pincodes_master (LOWER(state));
CREATE INDEX IF NOT EXISTS idx_pincodes_centroid  ON pincodes_master (centroid_lat, centroid_lng);


-- ----------------------------------------------------------------------------
-- 2. GEOGRAPHIC HIERARCHY  (district → sub-district → village)
-- ----------------------------------------------------------------------------
-- Source: combined.xlsx output of collate_villages.py
-- Used for the "Pick by area" dropdowns. No coordinates here.
CREATE TABLE IF NOT EXISTS geographic_hierarchy (
    id                  SERIAL PRIMARY KEY,
    state               VARCHAR(255),
    district            VARCHAR(255),
    sub_district        VARCHAR(255),
    village             VARCHAR(255),
    flag                VARCHAR(64),                  -- e.g., "MISSING: Village"
    loaded_at           TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hierarchy_district  ON geographic_hierarchy (LOWER(district));
CREATE INDEX IF NOT EXISTS idx_hierarchy_state     ON geographic_hierarchy (LOWER(state));
CREATE INDEX IF NOT EXISTS idx_hierarchy_sub       ON geographic_hierarchy (LOWER(district), LOWER(sub_district));


-- ----------------------------------------------------------------------------
-- 3. DISTRICT STATUS  (one-shot lock: DRAFT or FINALIZED)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS district_status (
    district            VARCHAR(255) PRIMARY KEY,
    state               VARCHAR(255) NOT NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT', 'FINALIZED')),
    finalized_by        VARCHAR(255),
    finalized_at        TIMESTAMP,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);


-- ----------------------------------------------------------------------------
-- 4. DISTRICT HUBS  (each virtual hub ops places)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS district_hubs (
    id                  SERIAL PRIMARY KEY,
    district            VARCHAR(255) NOT NULL,
    state               VARCHAR(255) NOT NULL,
    hub_name            VARCHAR(255) NOT NULL,
    hub_lat             DOUBLE PRECISION NOT NULL,
    hub_lng             DOUBLE PRECISION NOT NULL,
    radius_km           DOUBLE PRECISION NOT NULL CHECK (radius_km > 0),
    placement_method    VARCHAR(32),                  -- 'search' | 'area' | 'click'
    place_name          VARCHAR(255),                 -- if from search, the place searched
    sub_district        VARCHAR(255),                 -- if from area mode
    village             VARCHAR(255),                 -- if from area mode
    created_by          VARCHAR(255),
    created_at          TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (district) REFERENCES district_status(district)
);

CREATE INDEX IF NOT EXISTS idx_hubs_district ON district_hubs (LOWER(district));


-- ----------------------------------------------------------------------------
-- 5. DISTRICT COVERAGE  (pincodes computed for each hub via haversine)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS district_coverage (
    id                  SERIAL PRIMARY KEY,
    hub_id              INTEGER NOT NULL REFERENCES district_hubs(id) ON DELETE CASCADE,
    district            VARCHAR(255) NOT NULL,
    pincode             VARCHAR(10) NOT NULL,
    city                VARCHAR(255),
    state               VARCHAR(255),
    distance_km         DOUBLE PRECISION NOT NULL,
    is_excluded         BOOLEAN DEFAULT FALSE,        -- ops manual override (during DRAFT only)
    excluded_by         VARCHAR(255),
    excluded_at         TIMESTAMP,
    exclusion_reason    VARCHAR(500),
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coverage_hub      ON district_coverage (hub_id);
CREATE INDEX IF NOT EXISTS idx_coverage_district ON district_coverage (LOWER(district));
CREATE INDEX IF NOT EXISTS idx_coverage_pincode  ON district_coverage (pincode);


-- ----------------------------------------------------------------------------
-- USEFUL VIEWS for downstream queries
-- ----------------------------------------------------------------------------

-- Active (non-excluded) coverage for a district, deduplicated by pincode
CREATE OR REPLACE VIEW v_district_active_pincodes AS
SELECT
    c.district,
    c.state,
    c.pincode,
    c.city,
    MIN(c.distance_km)            AS nearest_hub_distance_km,
    COUNT(DISTINCT c.hub_id)      AS covering_hub_count,
    STRING_AGG(DISTINCT h.hub_name, ', ' ORDER BY h.hub_name) AS covering_hubs
FROM district_coverage c
JOIN district_hubs h ON h.id = c.hub_id
WHERE c.is_excluded = FALSE
GROUP BY c.district, c.state, c.pincode, c.city;


-- Full audit view including excluded rows
CREATE OR REPLACE VIEW v_district_full_audit AS
SELECT
    c.district, c.state,
    h.hub_name, h.hub_lat, h.hub_lng, h.radius_km,
    c.pincode, c.city,
    c.distance_km,
    c.is_excluded, c.excluded_by, c.excluded_at, c.exclusion_reason,
    c.created_at
FROM district_coverage c
JOIN district_hubs h ON h.id = c.hub_id;
