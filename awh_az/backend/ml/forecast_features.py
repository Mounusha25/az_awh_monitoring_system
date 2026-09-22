"""Shared feature engineering for the water-production forecast model.

Used by both forecast_train.py (offline training) and the /forecast serving
endpoint, so training and serving can never silently drift apart — the same
function computes the same feature for the same (as-of time, horizon) pair
either way.

Forecasting setup: "direct multi-horizon" regression. One row of hourly
history in, and instead of one model per horizon (1h ahead, 2h ahead, ...),
a single model takes the horizon (in hours) as a feature and predicts
water_produced_L that many hours after the "as-of" timestamp. This avoids
the accumulating-error problem of feeding a model's own 1-hour-ahead
predictions back in as input for the 2-hour-ahead prediction, and needs only
one trained artifact for the whole 1-48h range this station page will show.

No future weather (temperature/humidity) is used as a predictor, because
none is available at serving time. What IS used: past values of humidity/
temperature themselves (their recent trend has momentum — a rising intake
humidity trend tends to keep rising over the next day, which is legitimate
signal), lagged/rolling statistics of the water production series, and
calendar features of the *target* timestamp (known in advance, since it's
just calendar arithmetic).
"""

from __future__ import annotations

import math
from typing import Sequence

import pandas as pd

# How far back a lag/rolling feature is allowed to look. 168h = 1 week, the
# longest lag used (captures weekly patterns e.g. lab access schedules), so
# a row needs at least this much history before it to be usable for training
# or inference.
MAX_LOOKBACK_HOURS = 168

# Lags (in hours before the as-of time) applied to every signal column.
LAG_HOURS = [1, 3, 6, 24, 48, 168]

# Rolling-window statistics (up to and including the as-of hour) applied to
# every signal column.
ROLLING_WINDOWS_HOURS = [24, 168]

# The target series always gets lag/rolling features. Exogenous columns are
# genuinely-observed past readings (not future forecasts) that correlate
# physically with production — humidity has its own momentum a water-only
# model can't see. Std of humidity/temperature isn't included: the mean's
# trend is the useful part, and halving the feature count keeps the training
# table smaller for not much loss.
TARGET_COLUMN = "water_produced_L"
EXOGENOUS_COLUMNS = ["abs_humidity_intake_mean", "temperature_mean"]
SIGNAL_COLUMNS = [TARGET_COLUMN] + EXOGENOUS_COLUMNS


def _signal_feature_names(col: str) -> list[str]:
    names = [f"{col}_lag_{h}h" for h in LAG_HOURS]
    names += [f"{col}_rolling_mean_{w}h" for w in ROLLING_WINDOWS_HOURS]
    if col == TARGET_COLUMN:
        names += [f"{col}_rolling_std_{w}h" for w in ROLLING_WINDOWS_HOURS]
    return names


FEATURE_COLUMNS = (
    [name for col in SIGNAL_COLUMNS for name in _signal_feature_names(col)]
    + [
        "horizon_h",
        "target_hour_sin",
        "target_hour_cos",
        "target_dow_sin",
        "target_dow_cos",
        "target_month_sin",
        "target_month_cos",
    ]
)


def hourly_rows_to_frame(hourly_rows: Sequence[dict]) -> pd.DataFrame:
    """Converts /hourly's `data` list into a regular hourly-indexed
    DataFrame with one column per entry in SIGNAL_COLUMNS (float, NaN where
    missing). `/hourly` already returns one row per calendar hour in range
    (gaps included as null rows), but this re-indexes explicitly so lag/
    rolling arithmetic is correct even if that ever changes, or a caller
    passes a filtered subset."""
    if not hourly_rows:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    idx = pd.to_datetime([r["hour"] for r in hourly_rows], utc=True)
    data = {col: [r.get(col) for r in hourly_rows] for col in SIGNAL_COLUMNS}
    df = pd.DataFrame(data, index=idx).sort_index()
    full_idx = pd.date_range(df.index.min(), df.index.max(), freq="h")
    return df.reindex(full_idx)


def _cyclical(value: int, period: int) -> tuple[float, float]:
    angle = 2 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def _as_of_features(frame: pd.DataFrame, as_of_pos: int) -> dict | None:
    """Features knowable at the as-of hour (position as_of_pos in `frame`).
    Returns None if there isn't enough history before it yet."""
    if as_of_pos < MAX_LOOKBACK_HOURS:
        return None

    window = frame.iloc[as_of_pos - MAX_LOOKBACK_HOURS : as_of_pos + 1]
    feats: dict = {}
    for col in SIGNAL_COLUMNS:
        series = window[col]
        for h in LAG_HOURS:
            feats[f"{col}_lag_{h}h"] = series.iloc[-1 - h] if len(series) > h else None
        for w in ROLLING_WINDOWS_HOURS:
            recent = series.iloc[-w:]
            feats[f"{col}_rolling_mean_{w}h"] = recent.mean()
            if col == TARGET_COLUMN:
                feats[f"{col}_rolling_std_{w}h"] = recent.std()
    return feats


def _target_time_features(target_ts: pd.Timestamp, k: int) -> dict:
    hour_sin, hour_cos = _cyclical(target_ts.hour, 24)
    dow_sin, dow_cos = _cyclical(target_ts.dayofweek, 7)
    month_sin, month_cos = _cyclical(target_ts.month - 1, 12)
    return {
        "horizon_h": k,
        "target_hour_sin": hour_sin,
        "target_hour_cos": hour_cos,
        "target_dow_sin": dow_sin,
        "target_dow_cos": dow_cos,
        "target_month_sin": month_sin,
        "target_month_cos": month_cos,
    }


def build_training_table(
    frame: pd.DataFrame, horizons: Sequence[int]
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """One training example per (as-of hour, horizon) pair where both the
    as-of feature window and the target are available. Returns (X, y,
    as_of_timestamps) with X columns == FEATURE_COLUMNS — as_of_timestamps
    lets the caller do a time-based train/val/test split (by as-of time, not
    target time) without leaking future data into training."""
    rows: list[dict] = []
    targets: list[float] = []
    as_of_timestamps: list = []
    target_series = frame[TARGET_COLUMN]

    for as_of_pos in range(MAX_LOOKBACK_HOURS, len(frame)):
        as_of_feats = _as_of_features(frame, as_of_pos)
        if as_of_feats is None:
            continue
        as_of_ts = frame.index[as_of_pos]

        for k in horizons:
            target_pos = as_of_pos + k
            if target_pos >= len(frame):
                break
            target_val = target_series.iloc[target_pos]
            if pd.isna(target_val):
                continue

            target_ts = as_of_ts + pd.Timedelta(hours=k)
            rows.append({**as_of_feats, **_target_time_features(target_ts, k)})
            targets.append(target_val)
            as_of_timestamps.append(as_of_ts)

    X = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    y = pd.Series(targets, name=TARGET_COLUMN)
    return X, y, pd.Series(as_of_timestamps, name="as_of")


def build_forecast_features(frame: pd.DataFrame, horizons: Sequence[int]) -> pd.DataFrame | None:
    """Features for forecasting forward from the LAST timestamp in `frame`
    (the most recent hour with known history) for each horizon in
    `horizons`. Returns None if there isn't enough history yet."""
    as_of_pos = len(frame) - 1
    as_of_feats = _as_of_features(frame, as_of_pos)
    if as_of_feats is None:
        return None
    as_of_ts = frame.index[as_of_pos]

    rows = []
    for k in horizons:
        target_ts = as_of_ts + pd.Timedelta(hours=k)
        rows.append({**as_of_feats, **_target_time_features(target_ts, k)})
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)
