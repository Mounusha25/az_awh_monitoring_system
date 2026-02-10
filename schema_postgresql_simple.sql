-- Simple PostgreSQL Schema (no TimescaleDB required)
-- Fast setup for AWH ingestion worker

-- ============================================
-- Stations Table
-- ============================================

CREATE TABLE IF NOT EXISTS stations (
    station_id SERIAL PRIMARY KEY,
    station_name TEXT UNIQUE NOT NULL,
    location JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stations_name ON stations(station_name);

-- ============================================
-- Measurements Table (Regular Table)
-- ============================================

CREATE TABLE IF NOT EXISTS measurements (
    time TIMESTAMPTZ NOT NULL,
    station_id INT NOT NULL REFERENCES stations(station_id),
    
    -- Intake air
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    velocity DOUBLE PRECISION,
    unit TEXT,
    
    -- Outtake air
    outtake_temperature DOUBLE PRECISION,
    outtake_humidity DOUBLE PRECISION,
    outtake_velocity DOUBLE PRECISION,
    outtake_unit TEXT,
    
    -- Water collection
    weight DOUBLE PRECISION,
    pump_status INTEGER,
    
    -- Power metrics
    voltage DOUBLE PRECISION,
    power DOUBLE PRECISION,
    energy BIGINT,
    
    -- Flow metrics
    flow_rate DOUBLE PRECISION,
    flow_unit TEXT,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Unique constraint for idempotency: same (time, station_id) = same measurement
CREATE UNIQUE INDEX IF NOT EXISTS measurements_time_station_key 
    ON measurements (time, station_id);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_measurements_station_time 
    ON measurements (station_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_measurements_time 
    ON measurements (time DESC);

-- ============================================
-- Optional: Add partitioning by month for large datasets
-- ============================================
-- Uncomment after initial setup if you have 1M+ rows:
-- ALTER TABLE measurements SET (
--     autovacuum_vacuum_scale_factor = 0.01,
--     autovacuum_analyze_scale_factor = 0.005
-- );

-- ============================================
-- Setup Complete
-- ============================================
-- Tables created successfully!
-- You can now use the ingestion worker.

-- Monitor data:
SELECT 'Stations' as table_name, COUNT(*) as row_count FROM stations
UNION ALL
SELECT 'Measurements', COUNT(*) FROM measurements;
