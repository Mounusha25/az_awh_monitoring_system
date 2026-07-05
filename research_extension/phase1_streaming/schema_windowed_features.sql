-- windowed_features table — output of Spark Structured Streaming consumer
-- Each row = one 30-minute window (sliding every 5 min) for one station
-- 10 sensor features × 4 statistics = 40 measurement columns
--
-- Run ONCE before starting the Spark consumer:
--   psql -d awh_db -f schema_windowed_features.sql

CREATE TABLE IF NOT EXISTS windowed_features (
    id               SERIAL PRIMARY KEY,
    station_id       INT NOT NULL REFERENCES stations(station_id),
    window_start     TIMESTAMPTZ NOT NULL,
    window_end       TIMESTAMPTZ NOT NULL,
    record_count     INT,                   -- raw readings inside this window

    -- Intake air
    temperature_mean     DOUBLE PRECISION,
    temperature_std      DOUBLE PRECISION,
    temperature_min      DOUBLE PRECISION,
    temperature_max      DOUBLE PRECISION,

    humidity_mean        DOUBLE PRECISION,
    humidity_std         DOUBLE PRECISION,
    humidity_min         DOUBLE PRECISION,
    humidity_max         DOUBLE PRECISION,

    velocity_mean        DOUBLE PRECISION,
    velocity_std         DOUBLE PRECISION,
    velocity_min         DOUBLE PRECISION,
    velocity_max         DOUBLE PRECISION,

    -- Outtake air
    outtake_temperature_mean   DOUBLE PRECISION,
    outtake_temperature_std    DOUBLE PRECISION,
    outtake_temperature_min    DOUBLE PRECISION,
    outtake_temperature_max    DOUBLE PRECISION,

    outtake_humidity_mean      DOUBLE PRECISION,
    outtake_humidity_std       DOUBLE PRECISION,
    outtake_humidity_min       DOUBLE PRECISION,
    outtake_humidity_max       DOUBLE PRECISION,

    outtake_velocity_mean      DOUBLE PRECISION,
    outtake_velocity_std       DOUBLE PRECISION,
    outtake_velocity_min       DOUBLE PRECISION,
    outtake_velocity_max       DOUBLE PRECISION,

    -- Water collection
    weight_mean          DOUBLE PRECISION,
    weight_std           DOUBLE PRECISION,
    weight_min           DOUBLE PRECISION,
    weight_max           DOUBLE PRECISION,

    -- Power metrics
    voltage_mean         DOUBLE PRECISION,
    voltage_std          DOUBLE PRECISION,
    voltage_min          DOUBLE PRECISION,
    voltage_max          DOUBLE PRECISION,

    power_mean           DOUBLE PRECISION,
    power_std            DOUBLE PRECISION,
    power_min            DOUBLE PRECISION,
    power_max            DOUBLE PRECISION,

    energy_mean          DOUBLE PRECISION,
    energy_std           DOUBLE PRECISION,
    energy_min           DOUBLE PRECISION,
    energy_max           DOUBLE PRECISION,

    created_at           TIMESTAMPTZ DEFAULT NOW(),

    -- One row per station per window start
    CONSTRAINT windowed_features_station_window_key
        UNIQUE (station_id, window_start)
);

CREATE INDEX IF NOT EXISTS idx_wf_station_window
    ON windowed_features (station_id, window_start DESC);

CREATE INDEX IF NOT EXISTS idx_wf_window_start
    ON windowed_features (window_start DESC);

-- Verify
SELECT COUNT(*) AS windowed_feature_rows FROM windowed_features;
