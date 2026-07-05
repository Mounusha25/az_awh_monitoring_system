"""
AWH Spark Structured Streaming Consumer — Phase 1: Streaming Foundation

Reads sensor events from Kafka topic 'awh-sensor-readings', computes
windowed statistical features (mean, std, min, max) over 30-minute
sliding windows with a 5-minute step, and writes the result to the
PostgreSQL 'windowed_features' table.

These features feed directly into Phase 2 (LSTM + Isolation Forest models).

Start AFTER:
  1. Kafka is running:  docker compose up -d
  2. windowed_features table exists: psql -d awh_db -f schema_windowed_features.sql
  3. Producer is running: python producer.py

Run:
  python consumer.py

Spark downloads the Kafka connector JAR on first run (~150 MB, cached after).
Set SPARK_PACKAGES env var to override the connector version.
"""

import json
import os

import psycopg2
import psycopg2.extras
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType, IntegerType, StringType, StructField, StructType, TimestampType
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL    = os.getenv("DATABASE_URL",    "postgresql://mounusha@localhost:5432/awh_db")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC           = "awh-sensor-readings"

WINDOW_DURATION = "30 minutes"
SLIDE_DURATION  = "5 minutes"
WATERMARK       = "10 minutes"   # allow late events up to 10 min

# Kafka connector for PySpark 3.5.x (Scala 2.12)
SPARK_PACKAGES = os.getenv(
    "SPARK_PACKAGES",
    "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.4"
)

CHECKPOINT_DIR = os.getenv("SPARK_CHECKPOINT", "/tmp/awh-spark-checkpoint")

FEATURE_COLUMNS = [
    "temperature", "humidity", "velocity",
    "outtake_temperature", "outtake_humidity", "outtake_velocity",
    "weight", "voltage", "power", "energy",
]


# ---------------------------------------------------------------------------
# Schema for incoming Kafka JSON messages
# ---------------------------------------------------------------------------

MESSAGE_SCHEMA = StructType([
    StructField("station_id", IntegerType(), True),
    StructField("time",       StringType(),  True),  # ISO 8601 string → cast to timestamp
] + [
    StructField(col, DoubleType(), True)
    for col in FEATURE_COLUMNS
])


# ---------------------------------------------------------------------------
# Spark Session
# ---------------------------------------------------------------------------

def build_spark() -> SparkSession:
    return (
        SparkSession.builder
        .appName("AWH-StreamingConsumer")
        .config("spark.jars.packages", SPARK_PACKAGES)
        .config("spark.sql.streaming.schemaInference", "true")
        # Reduce Spark logging noise
        .config("spark.driver.extraJavaOptions", "-Dlog4j.rootCategory=WARN,console")
        .getOrCreate()
    )


# ---------------------------------------------------------------------------
# Write windowed results to PostgreSQL
# ---------------------------------------------------------------------------

def _upsert_batch(batch_df, _epoch_id):
    """
    Called by Spark's foreachBatch for each micro-batch.
    Converts the batch DataFrame to pandas and upserts into windowed_features.
    """
    rows = batch_df.toPandas()
    if rows.empty:
        return

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            for _, r in rows.iterrows():
                cur.execute(
                    """
                    INSERT INTO windowed_features (
                        station_id, window_start, window_end, record_count,
                        temperature_mean,   temperature_std,   temperature_min,   temperature_max,
                        humidity_mean,      humidity_std,      humidity_min,      humidity_max,
                        velocity_mean,      velocity_std,      velocity_min,      velocity_max,
                        outtake_temperature_mean, outtake_temperature_std,
                        outtake_temperature_min,  outtake_temperature_max,
                        outtake_humidity_mean,    outtake_humidity_std,
                        outtake_humidity_min,     outtake_humidity_max,
                        outtake_velocity_mean,    outtake_velocity_std,
                        outtake_velocity_min,     outtake_velocity_max,
                        weight_mean,   weight_std,   weight_min,   weight_max,
                        voltage_mean,  voltage_std,  voltage_min,  voltage_max,
                        power_mean,    power_std,    power_min,    power_max,
                        energy_mean,   energy_std,   energy_min,   energy_max
                    ) VALUES (
                        %(station_id)s, %(window_start)s, %(window_end)s, %(record_count)s,
                        %(temperature_mean)s, %(temperature_std)s, %(temperature_min)s, %(temperature_max)s,
                        %(humidity_mean)s,    %(humidity_std)s,    %(humidity_min)s,    %(humidity_max)s,
                        %(velocity_mean)s,    %(velocity_std)s,    %(velocity_min)s,    %(velocity_max)s,
                        %(outtake_temperature_mean)s, %(outtake_temperature_std)s,
                        %(outtake_temperature_min)s,  %(outtake_temperature_max)s,
                        %(outtake_humidity_mean)s,    %(outtake_humidity_std)s,
                        %(outtake_humidity_min)s,     %(outtake_humidity_max)s,
                        %(outtake_velocity_mean)s,    %(outtake_velocity_std)s,
                        %(outtake_velocity_min)s,     %(outtake_velocity_max)s,
                        %(weight_mean)s,   %(weight_std)s,   %(weight_min)s,   %(weight_max)s,
                        %(voltage_mean)s,  %(voltage_std)s,  %(voltage_min)s,  %(voltage_max)s,
                        %(power_mean)s,    %(power_std)s,    %(power_min)s,    %(power_max)s,
                        %(energy_mean)s,   %(energy_std)s,   %(energy_min)s,   %(energy_max)s
                    )
                    ON CONFLICT (station_id, window_start)
                    DO UPDATE SET
                        window_end   = EXCLUDED.window_end,
                        record_count = EXCLUDED.record_count,
                        temperature_mean   = EXCLUDED.temperature_mean,
                        temperature_std    = EXCLUDED.temperature_std,
                        temperature_min    = EXCLUDED.temperature_min,
                        temperature_max    = EXCLUDED.temperature_max,
                        humidity_mean      = EXCLUDED.humidity_mean,
                        humidity_std       = EXCLUDED.humidity_std,
                        humidity_min       = EXCLUDED.humidity_min,
                        humidity_max       = EXCLUDED.humidity_max,
                        velocity_mean      = EXCLUDED.velocity_mean,
                        velocity_std       = EXCLUDED.velocity_std,
                        velocity_min       = EXCLUDED.velocity_min,
                        velocity_max       = EXCLUDED.velocity_max,
                        outtake_temperature_mean = EXCLUDED.outtake_temperature_mean,
                        outtake_temperature_std  = EXCLUDED.outtake_temperature_std,
                        outtake_temperature_min  = EXCLUDED.outtake_temperature_min,
                        outtake_temperature_max  = EXCLUDED.outtake_temperature_max,
                        outtake_humidity_mean    = EXCLUDED.outtake_humidity_mean,
                        outtake_humidity_std     = EXCLUDED.outtake_humidity_std,
                        outtake_humidity_min     = EXCLUDED.outtake_humidity_min,
                        outtake_humidity_max     = EXCLUDED.outtake_humidity_max,
                        outtake_velocity_mean    = EXCLUDED.outtake_velocity_mean,
                        outtake_velocity_std     = EXCLUDED.outtake_velocity_std,
                        outtake_velocity_min     = EXCLUDED.outtake_velocity_min,
                        outtake_velocity_max     = EXCLUDED.outtake_velocity_max,
                        weight_mean    = EXCLUDED.weight_mean,
                        weight_std     = EXCLUDED.weight_std,
                        weight_min     = EXCLUDED.weight_min,
                        weight_max     = EXCLUDED.weight_max,
                        voltage_mean   = EXCLUDED.voltage_mean,
                        voltage_std    = EXCLUDED.voltage_std,
                        voltage_min    = EXCLUDED.voltage_min,
                        voltage_max    = EXCLUDED.voltage_max,
                        power_mean     = EXCLUDED.power_mean,
                        power_std      = EXCLUDED.power_std,
                        power_min      = EXCLUDED.power_min,
                        power_max      = EXCLUDED.power_max,
                        energy_mean    = EXCLUDED.energy_mean,
                        energy_std     = EXCLUDED.energy_std,
                        energy_min     = EXCLUDED.energy_min,
                        energy_max     = EXCLUDED.energy_max,
                        created_at     = NOW()
                    """,
                    dict(r)
                )
        conn.commit()
        print(f"[Consumer] Wrote {len(rows)} windowed feature rows (epoch {_epoch_id})")
    except Exception as e:
        conn.rollback()
        print(f"[Consumer] DB write error: {e}")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Streaming Pipeline
# ---------------------------------------------------------------------------

def build_agg_exprs():
    """Build mean/std/min/max aggregation expressions for all 10 features."""
    aggs = [F.count("*").alias("record_count")]
    for col in FEATURE_COLUMNS:
        aggs.extend([
            F.mean(col).alias(f"{col}_mean"),
            F.stddev(col).alias(f"{col}_std"),
            F.min(col).alias(f"{col}_min"),
            F.max(col).alias(f"{col}_max"),
        ])
    return aggs


def run_streaming(spark: SparkSession):
    # Read raw bytes from Kafka
    raw = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribe", TOPIC)
        .option("startingOffsets", "earliest")
        .option("maxOffsetsPerTrigger", 10_000)
        .load()
    )

    # Parse JSON payload
    parsed = (
        raw
        .selectExpr("CAST(value AS STRING) AS json_str", "timestamp AS kafka_ts")
        .select(
            F.from_json(F.col("json_str"), MESSAGE_SCHEMA).alias("data"),
            "kafka_ts",
        )
        .select("data.*", "kafka_ts")
        # Parse the ISO timestamp string to a proper Spark TimestampType
        .withColumn("event_time", F.to_timestamp("time"))
        .drop("time")
        # Watermark allows Spark to handle late-arriving records
        .withWatermark("event_time", WATERMARK)
    )

    # Build stat column names once
    stat_cols = ["record_count"]
    for col in FEATURE_COLUMNS:
        stat_cols += [f"{col}_mean", f"{col}_std", f"{col}_min", f"{col}_max"]

    # Sliding window aggregation: 30-min window, 5-min step, grouped by station
    windowed = (
        parsed
        .groupBy(
            F.window("event_time", WINDOW_DURATION, SLIDE_DURATION),
            F.col("station_id"),
        )
        .agg(*build_agg_exprs())
        # Flatten window struct to separate start/end columns
        .select(
            F.col("station_id"),
            F.col("window.start").alias("window_start"),
            F.col("window.end").alias("window_end"),
            *[F.col(c) for c in stat_cols],
        )
    )

    # Write to PostgreSQL via foreachBatch
    query = (
        windowed.writeStream
        .outputMode("update")          # emit updated aggregates as late data arrives
        .option("checkpointLocation", CHECKPOINT_DIR)
        .trigger(processingTime="60 seconds")
        .foreachBatch(_upsert_batch)
        .start()
    )

    print(f"[Consumer] Streaming started — window={WINDOW_DURATION}, step={SLIDE_DURATION}")
    print(f"[Consumer] Checkpoint: {CHECKPOINT_DIR}")
    print(f"[Consumer] Press Ctrl+C to stop")
    query.awaitTermination()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")

    try:
        run_streaming(spark)
    except KeyboardInterrupt:
        print("\n[Consumer] Interrupted — shutting down")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
