# AzAWH — Pending Tasks
> Last updated: June 2026. Split into hardware-dependent and code-only tasks.

---

## PART A — Needs Physical Station / Pi Access

### 1. Set Up udev Rules (stable port names — top priority before production)
Port assignments shift on every reboot. Fix permanently with udev rules.

**Commands to run on Pi first (paste output to Claude):**
```bash
udevadm info -a -n /dev/ttyUSB0 | grep -E 'ATTRS{serial}|ATTRS{idVendor}|ATTRS{idProduct}'
udevadm info -a -n /dev/ttyUSB1 | grep -E 'ATTRS{serial}|ATTRS{idVendor}|ATTRS{idProduct}'
udevadm info -a -n /dev/ttyUSB2 | grep -E 'ATTRS{serial}|ATTRS{idVendor}|ATTRS{idProduct}'
ls -la /dev/serial/by-id/
```
Then Claude writes `/etc/udev/rules.d/99-awh-sensors.rules` and updates all Python DEFAULT_PORT values.

**Current (fragile) port assignments as of June 2026:**
- ttyUSB0 → Outtake anemometer (cp210x)
- ttyUSB1 → Power meter FTDI RS485
- ttyUSB2 → Intake anemometer (cp210x)
- Balance → by-id symlink (already stable)

### 2. Confirm Outtake Anemometer on ttyUSB0
```bash
cd ~/RPi_USB_Package && source .venv/bin/activate
python3 test_system/test_outtake_anemometer.py
```
Expected: prints temperature, humidity, velocity readings.

### 3. Run Full Station End-to-End
After outtake confirmed:
```bash
python3 AquaPars1_new_pm.py
```
Watch for all 5 sensors reporting data, no errors in first 2 minutes.

### 4. Test Flow Meter
```bash
python3 test_system/test_flow.py
```
Note: requires active water flow through the sensor to produce non-zero readings.

---

## PART B — Code Fixes (No Pi Needed)

### Done in this session:
- [x] Fixed baud rate: 2400 → 9600 for new DEM730P meter
- [x] Fixed Modbus address: 1 (last 2 digits of serial number)
- [x] Fixed serial port close: instrument.close() → instrument.serial.close()
- [x] Fixed serial port not released before retry in _run() exception handler
- [x] Added RS485 RTS direction control for FTDI adapter
- [x] Updated DEFAULT_PORT from Prolific by-id to FTDI by-id in read_power_new.py
- [x] Updated outtake_anemometer.py DEFAULT_PORT: ttyUSB3 → ttyUSB0
- [x] Created scan_powermeter.py (brute-force Modbus scanner)
- [x] Created debug_powermeter.py (raw byte RS485 diagnostic)

### Still to fix:

#### B1. awh_ui_layout.py — Pump Status Always Shows ON
**File:** `RPi_USB_Package/awh_ui_layout.py` line ~359
**Bug:** `bool("OFF")` returns `True` (non-empty string is truthy). Pump always shows ON.
**Fix:** Change to `str(status).strip().upper() in ("1", "ON", "TRUE")`

#### B2. awh_ui_layout.py — Config Dropdowns Start Locked
**File:** `RPi_USB_Package/awh_ui_layout.py` lines 223, 233, 247, 257
**Bug:** All 4 comboboxes initialized with `state="disabled"` — operator can't set config before starting.
**Fix:** Change initial state to `state="readonly"` on all 4 comboboxes.

#### B3. intake_anemometer.py — No Timeout (Thread Blocking Risk)
**File:** `RPi_USB_Package/intake_anemometer.py`
**Bug:** No timeout on serial read loop. If USB drops, the thread blocks forever and the UI/station hangs.
**Fix:** Add same timeout pattern already in outtake_anemometer.py (2s timeout, return None on expiry).

#### B4. ingestion_worker.py — StationManager Corrupts station_id
**File:** `ingestion_worker.py`
**Bug:** `ON CONFLICT (station_name) DO UPDATE SET station_id = EXCLUDED.station_id` — updating a primary key on conflict corrupts foreign key relationships in the measurements table.
**Fix:** Remove `SET station_id = EXCLUDED.station_id` from the ON CONFLICT clause. Only update non-key fields (station_name is the conflict target, station_id should never change).

#### B5. Schema Column Mismatch — Flow Fields
**Files:** `schema_postgresql_simple.sql`, `schema_timescaledb.sql`
**Bug:** SQL schema defines `flow_rate` and `flow_unit` but ingestion worker and Pydantic models use `flow_lmin`, `flow_hz`, `flow_total`. Live data is silently dropped.
**Fix:** Update schema to match code: rename `flow_rate` → `flow_lmin`, replace `flow_unit` with `flow_hz` and `flow_total`.

#### B6. Schema — Energy Column Wrong Type
**Files:** `schema_postgresql_simple.sql`, `schema_timescaledb.sql`
**Bug:** `energy` column is `BIGINT` but the DEM730P returns kWh as a float (e.g. 12.45). Values are truncated.
**Fix:** Change `energy BIGINT` → `energy FLOAT`.

#### B7. Merge AquaPars1.py and AquaPars1_new_pm.py
**File:** `RPi_USB_Package/AquaPars1.py` and `RPi_USB_Package/AquaPars1_new_pm.py`
**Bug:** Two near-identical files. Maintenance burden — any bug fix needs to be applied twice.
**Fix:** Keep `AquaPars1_new_pm.py` as the canonical file, rename to `AquaPars1.py`, archive the old one.

#### B8. read_power_new.py — NoneType Error on Stop
**File:** `RPi_USB_Package/read_power_new.py`
**Bug:** When `stop()` is called while `_run()` thread is mid-poll, `_instrument` is set to None before the thread checks it. Causes `'NoneType' object cannot be interpreted as an integer`.
**Fix:** Add `self._running` check before using `self._instrument` in `_run()` loop.

---

## PART C — Research Extension (Summer 2026)
Per CLAUDE.md Section 9.
- Week 1–2: Kafka + Spark streaming layer — ✅ built (`research_extension/phase1_streaming/`)
- Week 2: Benchmark dataset (labeled anomalies, train/val/test split) — ✅ built, synthetic fault injection (`research_extension/phase2_models/build_benchmark_dataset.py`)
- Week 3–4: LSTM + Isolation Forest models — IN PROGRESS, see below
- Week 4–5: LangGraph multi-agent system — not started
- Week 6–7: Evidently AI + Airflow MLOps pipeline — not started
- Week 8: Kubernetes + Grafana deployment — not started

### Phase 2 model status (updated 2026-07-15, second pass)

Current best RQ1 result: Isolation Forest ensemble, **attribution F1 = 0.437, 95% bootstrap CI
[0.335, 0.519]** (fault-instance-level cluster bootstrap, n=200) on
`research_extension/phase2_models/data/test.parquet`, vs. proposal target F1 > 0.80. Point estimate up
from 0.184 at the start of the 2026-07-14/15 diagnostic session, but treat single-run point estimates
with caution from here on — see the CI-width note below.

**Two rounds of fixes applied.** Round 1: removed overlapping fault labels; stabilized the small/noisy
eval set with more faults; added `{feature}_missing_frac` (dropout signal was being silently averaged
away); replaced absolute window stats with stats relative to a rolling per-station/per-feature baseline
(fixed cross-feature score comparability and a supervised classifier's generalization failure across the
time-based split); added `{feature}_max_run_frac` (stuck_at — a frozen sensor barely moves mean/std, but
"% of window that's one repeated value" catches it directly); switched fault labeling from "any overlap
counts" to `MIN_OVERLAP_FRACTION = 0.5` normalized by `min(fault_duration, window_duration)` (normalizing
by fault duration alone made faults longer than the window mathematically unlabelable — a bug caught and
fixed mid-session); increased independent fault instances 141 → 197.

Round 2 (external review + fixes): added `{feature}_rel_slope` (second-half-mean minus first-half-mean,
targets drift's ramp shape specifically — snapshot stats like rel_mean miss a gradual trend); replaced
z-score normalization with **percentile-rank** scoring in `isolation_forest_model.py` /
`joint_detector.py` (comparable across differently-shaped/scaled feature distributions, e.g. bounded
missing_frac vs. unbounded rel_mean, without assuming Gaussian tails); added `fault_id` tracking through
`inject_synthetic_faults.py` → `label_windows()` so evaluation can resample by fault instance, not by
window; added `evaluate.py::bootstrap_ci()` for exactly that.

**Per-fault-type breakdown (test set, Isolation Forest ensemble, single run — not bootstrap-averaged):**
| Fault type | Detection recall | Attribution accuracy (given detected) |
|---|---|---|
| spike | 100% | 100% |
| dropout | 15% | 100% |
| stuck_at | 34% | 100% |
| drift | 37% | 86% |

Compare to round 1's numbers (spike 100/100, dropout 76/100, stuck_at 60/100, drift 59/61): `rel_slope`
clearly helped drift's attribution (61%→86%) but detection recall dropped broadly across fault types.
**Given the bootstrap CI width (~0.15-0.20 F1 points on ~197 independent faults), this recall drop has
not been confirmed as real vs. noise from the threshold/candidate-grid change** — don't over-index on it
without more runs (different seeds, or a proper k-fold over fault instances).

**Diagnosed why two-stage (F1 0.313) and the supervised classifier (F1 0.222) underperform the ensemble:**
both have far worse false-positive rates on normal windows — two-stage 52%, classifier 47.5%, vs. the
ensemble's 12%. Root cause for two-stage: the joint `IsolationForestDetector` fits one forest over all 70
columns (10 features × 7 stats), and in that dimensionality it barely separates normal from anomalous —
this is a curse-of-dimensionality problem, not fixable by re-threshold alone; would need dimensionality
reduction (PCA / feature selection) before the joint forest to be worth pursuing further.

**Next step:** don't chase more feature engineering yet. Priorities in order: (1) confirm whether the
recall regression above is real by re-running with a different seed or doing a fault-instance k-fold,
since current evidence is inconclusive; (2) if real, investigate per-feature (not just per-ensemble)
threshold tuning, since percentile-rank scoring makes features *comparable* but a single shared cutoff
across all 10 can still be a bad fit if their separability genuinely differs; (3) LSTM remains explicitly
deferred — temporal shape is the right tool for drift specifically, but cheaper options aren't exhausted
yet, and sparse-station data (only stations 3 and 6 have volume) is still a real constraint on it.
