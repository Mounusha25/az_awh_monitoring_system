"""
AWH Phase 3 — run the multi-agent pipeline over sample windows.

Pulls a mix of real anomalous/normal windows from the Phase 2 test split
(so SensorDriftAgent runs on genuine model input), fetches each window's
real raw readings from Postgres for ThresholdBreachAgent, and adds one
synthetic out-of-bounds case so the threshold path actually fires at least
once in the demo (real station readings rarely leave the placeholder
sanity bounds — see threshold_breach_agent.py).

Usage:
  python run_pipeline.py [--n-anomalous 2] [--n-normal 2]
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import psycopg2

PHASE2_DIR = os.path.join(os.path.dirname(__file__), "..", "phase2_models")
sys.path.insert(0, PHASE2_DIR)

from data_prep import all_stat_columns  # noqa: E402

from graph import build_graph  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mounusha@localhost:5432/awh_db")
DATA_DIR = os.path.join(PHASE2_DIR, "data")
FEATURES = ["temperature", "humidity", "weight", "power"]


def fetch_raw_readings(conn, station_id: int, window_start, window_end) -> dict:
    """Mean of each of the 4 target features' raw readings within the window."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {", ".join(f"avg({f})" for f in FEATURES)}
            FROM measurements
            WHERE station_id = %s AND time >= %s AND time < %s
            """,
            (station_id, window_start, window_end),
        )
        row = cur.fetchone()
    return dict(zip(FEATURES, [float(v) if v is not None else None for v in row]))


def build_state(row: pd.Series, raw_readings: dict) -> dict:
    stat_row = {col: row[col] for col in all_stat_columns()}
    return {
        "station_id": int(row["station_id"]),
        "window_start": str(row["window_start"]),
        "window_end": str(row["window_end"]),
        "raw_readings": raw_readings,
        "stat_row": stat_row,
    }


def print_result(label: str, final_state: dict):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    print(f"station={final_state['station_id']}  window={final_state['window_start']} - {final_state['window_end']}")
    print(f"raw readings: {final_state['raw_readings']}")
    drift = final_state.get("drift")
    threshold = final_state.get("threshold")
    print(f"SensorDriftAgent:      {drift}")
    print(f"ThresholdBreachAgent:  {threshold}")
    if final_state.get("needs_incident"):
        print(f"\nIncidentReportAgent:\n{final_state.get('incident_report')}")
        print(f"\nStakeholderEscalationAgent: {final_state.get('escalation')}")
    else:
        print("\n(No anomaly or breach — graph terminated after merge, no report generated.)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-anomalous", type=int, default=2)
    parser.add_argument("--n-normal", type=int, default=1)
    args = parser.parse_args()

    test_df = pd.read_parquet(os.path.join(DATA_DIR, "test.parquet"))
    anomalous = test_df[test_df["is_anomaly"]].sample(min(args.n_anomalous, test_df["is_anomaly"].sum()), random_state=1)
    normal = test_df[~test_df["is_anomaly"]].sample(min(args.n_normal, (~test_df["is_anomaly"]).sum()), random_state=1)
    sample = pd.concat([anomalous, normal])

    app = build_graph()
    conn = psycopg2.connect(DATABASE_URL)
    try:
        for _, row in sample.iterrows():
            raw_readings = fetch_raw_readings(conn, int(row["station_id"]), row["window_start"], row["window_end"])
            state = build_state(row, raw_readings)
            label = f"REAL window (true cause: {row['causal_parameter']})"
            result = app.invoke(state)
            print_result(label, result)

        # Synthetic out-of-bounds case so ThresholdBreachAgent fires at least once —
        # real station readings rarely leave the placeholder sanity bounds.
        synthetic_row = sample.iloc[0].copy()
        synthetic_state = build_state(synthetic_row, {"temperature": -15.0, "humidity": 92.0, "weight": 4500.0, "power": 610.0})
        result = app.invoke(synthetic_state)
        print_result("SYNTHETIC out-of-bounds case (temperature=-15C, demo only)", result)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
