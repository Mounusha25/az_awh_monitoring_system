-- PostgreSQL + TimescaleDB Schema Setup
-- Run this script to initialize the database for AWH ingestion

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ============================================
-- Stations Table
-- ============================================

CREATE TABLE IF NOT EXISTS stations (
    station_id SERIAL PRIMARY KEY,
    station_name TEXT UNIQUE NOT NULL,
    location TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stations_name ON stations(station_name);

-- ============================================
-- Measurements Hypertable (TimescaleDB)
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
    flow_lmin DOUBLE PRECISION,
    flow_hz DOUBLE PRECISION,
    flow_total DOUBLE PRECISION
);

-- Create unique constraint for idempotency
ALTER TABLE measurements ADD CONSTRAINT measurements_time_station_key
UNIQUE (time, station_id);

-- Convert to hypertable if not already
SELECT create_hypertable(
    'measurements',
    'time',
    if_not_exists => TRUE
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_measurements_station_time
ON measurements (station_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_measurements_time
ON measurements (time DESC);

-- ============================================
-- Compression (optional, for older data)
-- ============================================

-- Compress segments older than 30 days
ALTER TABLE measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'station_id'
);

SELECT add_compression_policy(
    'measurements',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

-- ============================================
-- Data Retention Policy (optional)
-- ============================================

-- Keep 2 years of raw data, older data can be archived
SELECT add_retention_policy(
    'measurements',
    INTERVAL '2 years',
    if_not_exists => TRUE
);

-- ============================================
-- Verification Queries
-- ============================================

-- Verify schema
SELECT * FROM information_schema.tables
WHERE table_name IN ('stations', 'measurements');

-- Verify hypertable
SELECT * FROM timescaledb_information.hypertables
WHERE hypertable_name = 'measurements';

-- Verify indexes
SELECT * FROM pg_indexes
WHERE tablename = 'measurements';
