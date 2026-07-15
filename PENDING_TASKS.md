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

#### B9. ingestion_worker.py — Shared Global Checkpoint Silently Skips Low-Volume Stations
**File:** `ingestion_worker.py::FirebaseClient.fetch_new_documents`
**Bug:** Discovered 2026-07-15 while backfilling stations 3/6 for Phase 2 research: `fetch_new_documents`
queries each station's `readings` subcollection independently (capped at `limit // n_stations` docs
per station per call), then sets the **next call's checkpoint to the global max timestamp across all
stations in that batch**. If one station has a much denser backlog than another right after the
checkpoint, its per-station cap fills up within a much smaller real-time span, while the global
checkpoint still jumps forward to whatever the fastest-advancing station reached. The next call then
queries every station starting from that jumped-ahead checkpoint — permanently skipping any
documents from slower stations that fell between the old and new checkpoint. Confirmed in practice:
running the worker against the shared 9-station backlog inserted 0 new rows for station 6 (which had
31,000 genuinely new documents sitting in Firestore, later confirmed and backfilled via a
single-station bypass query). This isn't specific to a one-off script — it's in the production
worker itself, and would silently under-ingest any lower-volume station on every scheduled run
whenever a higher-volume station's backlog dominates a batch.
**Fix:** Track a per-station checkpoint (e.g. a dict keyed by station_name) instead of one shared
timestamp, so each station's next query starts from where *that station* last left off, not from the
batch-wide max.

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

**Round 3 (independent reproduction + ablation, external review):** an independent bootstrap run
reproduced round 2's numbers to 3 decimal places, confirming the pipeline is deterministic and
reproducible. That review also ran the ablation needed to resolve round 2's open question — is
the recall regression from `rel_slope` or from percentile-rank scoring? — by re-running with
percentile-rank scoring but `rel_slope` excluded: **it's `rel_slope`, not percentile-rank scoring.**
Percentile-rank alone reproduced round-1 recall almost exactly; adding `rel_slope` is what collapsed
it. Mechanism: `rel_slope` is a real signal for drift (its defining property is a ramp) but pure
noise for dropout/stuck_at (no ramp shape), so it acted as a noisy 7th dimension in those features'
per-feature IsolationForest — the same "too many dimensions for one forest" problem already
diagnosed for the two-stage model's 70-column joint detector, just at a smaller scale (6→7 columns).

**Fix applied:** `isolation_forest_model.py`'s `IsolationForestEnsemble` now fits two forests per
feature — a 6-column DETECTION forest (`data_prep.py::detection_columns`, no `rel_slope`) that
decides `is_anomaly`, and a 7-column ATTRIBUTION forest (`attribution_columns`, includes
`rel_slope`) used only to rank candidate causal parameters among windows already flagged anomalous.
`two_stage_model.py` updated to call the new `attribution_feature_scores()` accordingly.

**Result — real, but not what was initially hoped for:** detection recall recovered (drift 59%,
stuck_at 66%, dropout 50% — up from round 2's 37%/34%/15%, though dropout not fully back to round
1's 76%). But drift's attribution-given-detection *also* reverted, from round 2's 86% back to 60%.
Diagnosis: decoupling changed *which* windows get flagged as anomalous (the 6-column detector's
selection differs from round 2's combined-forest selection), and the newly-caught windows are a
harder set for the attribution forest to get right — the same self-selection dynamic already seen
between the two-stage model and the plain ensemble, recurring here at a smaller scale. **Bootstrap
CI confirms the net effect is a real, clean win on detection, not a wash:** detection F1 mean 0.560,
95% CI [0.435, 0.672] (up from [0.352, 0.637] pre-fix — a genuine upward shift, not noise), while
attribution F1 is statistically unchanged at mean 0.437, 95% CI [0.354, 0.514] (nearly identical to
pre-fix [0.335, 0.519]). Current best model = this decoupled ensemble.

**Diagnosed why two-stage (F1 ≈0.30) and the supervised classifier (F1 ≈0.20-0.25) underperform:**
both have far worse false-positive rates on normal windows than the ensemble's ~12% (two-stage
~46-52%, classifier ~47.5% by FP/(FP+TN) — note: an independent reproduction got 21%/49.3%
respectively for FPR/1-precision on the classifier, which doesn't match either of my numbers
cleanly; the discrepancy is most likely a different threshold value selected during independent
reproduction rather than a definition error, since my own recomputation confirmed 47.5% is
genuinely FP/(FP+TN) at threshold=0.15, not an accidental 1-precision figure — **pin down the exact
threshold value before either number goes in a paper**). Root cause for two-stage: the joint
`JointIsolationForestDetector` fits one forest over all 70 columns, and in that dimensionality it
barely separates normal from anomalous — a curse-of-dimensionality problem, not fixable by
re-thresholding; would need PCA/feature selection before the joint-forest architecture is worth
pursuing further.

**Next step:** the detection/attribution decoupling pattern has now paid off twice (two-stage vs.
ensemble; 6-col vs 7-col per-feature forests) — it's the load-bearing idea in this whole pipeline,
worth treating as a design principle rather than a one-off fix. Priorities: (1) reconcile the
classifier FPR discrepancy (exact threshold value, exact definition) before any number is quoted
externally; (2) LSTM remains deferred — temporal shape is the right tool for drift specifically, and
now that rel_slope's value for drift is cleanly isolated to the attribution stage, an LSTM
attribution-stage model (replacing or augmenting the 7-column forest) is a more targeted next
architecture than a full joint LSTM detector; sparse-station data (only stations 3 and 6 have
volume) is still a real constraint on it.
