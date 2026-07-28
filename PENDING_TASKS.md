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

#### B9. ingestion_worker.py — Shared Global Checkpoint Silently Skips Low-Volume Stations [FIXED 2026-07-27]
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

**Fixed 2026-07-27:** `CheckpointManager.load()`/`.save()` now store `{station_name: timestamp}`
instead of one shared timestamp; `FirebaseClient.fetch_new_documents()` queries each station using
its own checkpoint value; `IngestionWorker.run_once()` only advances the stations present in a given
batch, leaving every other station's checkpoint untouched. An old-format single-timestamp checkpoint
file (if one exists in production) is auto-migrated on first load to a `_legacy_default` fallback
used only for stations without their own entry yet — no manual backfill needed on upgrade, and no
reset to epoch. `test_ingestion_worker.py`'s `TestCheckpointManager` updated to match (17/17 passing),
plus two new tests for the migration path and per-station independence.

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

**Round 4 (2026-07-15, later same day) — scoped the benchmark to stations 3 and 6 only.**
Rationale: the other 6 stations were attempted across all four rounds above but never contributed
more than 6-44 windows each (lightly-used test/dev units, not continuously running) — see
[[dataset_station_coverage_gap]]. Restricting to the 2 continuously-running stations is a defensible,
deliberate research choice, not a compromise, and **this is the honest way to describe it in any
methods section**: attempted all 8, found 6 had negligible usable volume, scoped down explicitly.
Do not write "used all 8 stations" anywhere — the fault log and code make the actual 2-station
concentration immediately checkable, and a mismatched methods claim is exactly the kind of thing a
committee/reviewer catches easily and weighs heavily against credibility.

**What changed operationally:** discovered `ingestion_worker.py`'s shared global checkpoint was
silently skipping station 6 entirely (see B9 above) — backfilled it directly, bringing station 6 from
20,278 to 44,317 rows (Sep 2025-Jul 2026) and station 3 from 20,253 to 24,869 (Aug 2025-Jul 2026).
Added `INCLUDED_STATIONS` / `--stations` scoping to `build_benchmark_dataset.py`. Rebuilt: 317
independent fault instances (up from 197), 14,064 total windows, all from these 2 stations.

**Result — genuinely mixed, not a clean win, verified by independent reproduction to the decimal:**
detection F1 statistically unchanged (0.544 [0.409, 0.647] vs the 8-station scope's 0.560 [0.435,
0.672]), but **attribution F1 dropped and the CIs barely overlap** — 0.297 [0.198, 0.388] vs 0.437
[0.354, 0.514], a real difference. Per-fault-type: spike detection recall collapsed to 50% (was 100%
in every prior round) and stuck_at attribution collapsed to 22.6% (was 100%). False-positive rate on
normal windows rose to 28.8% (from 12%).

**Investigated but not resolved:** tested the hypothesis that the extra ~2 months of newly-backfilled
data introduced real sensor calibration drift into the test period (a later, more-extended time range
than any prior split). Evidence is mixed, not confirmatory: comparing val (Jun 14-21, closer to
train) vs test (Jun 21-Jul 13) recall, drift and stuck_at were flat/slightly up (71.1%→71.7%,
36.8%→40.8%) while spike and dropout declined (79.8%→50.0%, 58.3%→44.1%). If genuine calibration
drift were driving this, all four fault types should degrade together — they don't, which weakens
(but doesn't rule out) the drift story. An equally plausible competing explanation: test-set spike
count dropped to just 24 windows (vs 84 in val) — small per-fault-type samples can swing recall
10-20 points by chance alone, consistent with the CI-width fragility already established earlier in
this document. **Not settled either way.**

**Next step (fresh session, not a quick check):** the test that would actually settle this is a
rotating time-split (k-fold across different train/val/test cuts) to see whether degradation follows
wherever the split boundary falls (supports drift) or stays tied to which specific fault instances
land in which split (supports small-sample fragility). Until that's run, treat both the 8-station and
2-station numbers as provisional and don't pick one as "the" result for the paper. Secondary,
still-open items from round 3 (classifier FPR reconciliation, LSTM at the attribution stage) remain
queued behind this.

**Round 5 (2026-07-27) — rotating k-fold run, question settled: small-sample fragility, not drift.**
Added `research_extension/phase2_models/rotating_kfold_eval.py` (walk-forward, 5 folds, 50% warmup,
IsolationForestEnsemble only — this is a benchmark-methodology diagnostic, not a model bake-off).
2-station scope, same fault-injection pipeline, same model. Per-fold attribution F1: 0.314, 0.100,
0.400, 0.425, 0.297 across folds 0-4 (test windows chronologically later each fold). Results:

- **Non-monotonic, weak wrong-signed trend:** fold-index vs attribution_f1 correlation = +0.360
  (drift predicts strongly *negative*). Not a declining trend at all.
- **Fold 1 attribution collapse (F1=0.100) is an isolated blip, not a trend start:** spike and
  stuck_at attribution-given-detected both hit 0.000 in fold 1 despite 100% detection recall, then
  recover to 0.67-1.00 by fold 3 — a real physical drift wouldn't reverse itself two folds later.
- **Detection recall dips in fold 3 (drift 0.71, dropout 0.74, spike 0.83, stuck_at 0.47) then fully
  recovers to ~1.00 in fold 4 across every fault type.** Fold 3 also has the fewest test fault
  instances of any fold (21, vs 31-33 elsewhere) — consistent with a small-sample dip, not
  consistent with progressive calibration drift (which doesn't self-correct).
- Per-fault-type attribution-given-detected swings 4x+ fold to fold on single-digit-to-low-double-
  digit fault counts per type per fold — exactly the fragility signature described in Round 4.

**Conclusion:** the 2-station attribution F1 drop (0.297 vs 8-station's 0.437) is small-sample
fragility from the benchmark split, not genuine sensor calibration drift. This resolves the Round 4
open question but does NOT resolve the underlying problem — attribution F1 is still far below the
RQ1 target (>0.80) under either scope, and remains genuinely low. Do not read this result as
validating either the 8-station or 2-station number as "the" one to report; it only says the gap
*between* them isn't drift. Per-fold results: `research_extension/phase2_models/data/
rotating_kfold_results.csv`; per-fault-type breakdown: `.../data/rotating_kfold_breakdown.csv`.

**Next step:** the low absolute attribution F1 (0.10-0.43 depending on fold/scope) is the real open
problem now that drift is ruled out. Candidates worth investigating next: (a) more fault instances
per benchmark build (each fold here only has 20-35 test instances split across 4 fault types, i.e.
5-10 per type — that's thin regardless of drift/fragility), (b) revisit the supervised classifier
FPR reconciliation still queued from round 3, (c) LSTM at the attribution stage, still not attempted.

**Round 6 (2026-07-27) — root-caused the attribution mechanism itself; two targeted fixes both
made it worse (real negative result, not abandoned prematurely).**

Confusion matrix on the standard test split (isolation forest ensemble, current best, attribution
F1=0.331) showed a striking pattern: `energy` and `weight` **never** win the cross-feature
attribution argmax, not even by mistake on an unrelated fault — 0/50 energy faults and 0/41 weight
faults attributed to the right parameter, and neither ever appears as a predicted label at all.
Root cause, confirmed by comparing median attribution percentile-rank score on strictly-NORMAL
windows across splits: on train-normal it's ~0.50 everywhere (calibration is circular by
construction), but on val/test-normal it jumps to 0.86-0.98 for `power`/`energy`/`velocity`/
`voltage`/`temperature` — i.e. those features look "anomalous" by the train-calibrated percentile
rank on genuinely normal later-period data, so they win the argmax almost by default regardless of
true cause. `energy`'s own score during real energy faults is actually high in absolute terms
(mean 0.972) — it loses because `power` is *even higher* almost everywhere, not because energy's
signal is weak.

**Two fixes tried, both regressed test performance — a real, reproducible negative result:**
1. One-shot recalibration against val-normal instead of train-normal
   (`IsolationForestEnsemble.recalibrate()`, added to `isolation_forest_model.py` but NOT wired
   into `train_phase2_models.py`): test detection F1 0.543→0.477, attribution F1 0.331→0.271.
   Diagnosis: val-normal and test-normal medians *also* disagree with each other per feature
   (e.g. humidity 0.729 in val vs 0.949 in test) — drift is continuous through the whole
   deployment, not a single train→eval step change, so any single fixed snapshot is already stale
   by the time it's used.
2. Rolling/causal percentile-rank calibration against a trailing 200-window buffer of
   ground-truth-normal windows, walking train→val→test in time order per station
   (`experiment_rolling_calibration.py`, standalone, not wired into the pipeline): test detection
   F1 0.543→0.477 (same number as fix 1 — worth independently confirming that's not a bug),
   attribution F1 0.331→0.202, i.e. *worse than the one-shot fix*.

**Interpretation — the calibration-drift theory doesn't fully explain the ceiling.** Both fixes
correctly do what they claim (verified: recalibration measurably de-inflates score medians), but
neither recovers separability on test; both actively hurt it, and detection recall pinned at 1.00
with F1 collapsing to precision in both cases suggests recalibrating against ANY non-train
population homogenizes/compresses the score distributions rather than making them more comparable.
Working hypothesis for next session: the "loud" features may carry a real, physically-driven,
elevated baseline variance in val/test periods (not purely an artifact), and normalizing it away
erases actual detection signal along with the drift artifact. The independent-per-feature argmax
race itself may be the more fundamental limitation — 10 features scored in isolation can't account
for real physical co-movement between e.g. intake/outtake channels or power/energy, so the
"loudest" feature in a given window may just be whichever one happens to have the widest natural
variance that period, not the one a physically-informed model would flag as causal.

**Also discovered, unrelated but should be fixed in the split logic:** `val.parquet` contains ZERO
station-3 rows (only station 6) — station 3's usable date range apparently ends before the 70-85%
quantile window that defines `val`. Every threshold tuned "on val" across every prior round has
therefore been tuned blind to station 3.

**Round 7 (2026-07-27) — found and fixed two foundational benchmark bugs; supersedes every F1
number reported before this round.** Went to do the round-3-queued supervised classifier FPR
reconciliation and found something bigger underneath it.

1. **`val.parquet` had zero station-3 rows** (noted at the end of round 6). Root cause: the
   train/val/test split computed ONE GLOBAL time quantile across both stations' combined windows;
   with station 6 contributing far more windows than station 3, the global 70th/85th percentile cut
   landed in a low-density stretch for station 3, leaving it with zero rows in that slice. Fixed in
   `build_benchmark_dataset.py::time_based_split` — quantile cutoffs are now computed per station
   before concatenating, so every station with enough windows contributes to train/val/test. Every
   "tuned on val" threshold in every round before this one was silently blind to station 3.

2. **Bigger bug: realized anomaly fraction was ~55%, not the intended ~17.5% target, and had been
   since round 1.** `AVG_WINDOWS_PER_FAULT = 4` (used to size how many faults to inject to hit the
   target fraction) was set before `MIN_OVERLAP_FRACTION` was tightened to 0.5 in round 1, and was
   never recalibrated afterward despite its own comment saying to. Measured empirically: 12.6-13.1
   windows flagged per fault under the current labeling rule, not 4 — a ~3.2x underestimate that
   silently over-injected faults by the same factor on every single dataset build since round 1.
   **Consequence: every "detection F1" number reported in every round of this document was close to
   meaningless.** With 55% of test windows anomalous, "always predict anomalous" scores detection
   F1 = 0.711 by arithmetic alone (precision=0.552, recall=1.0) — and that's almost exactly where
   the supervised classifier landed (0.711) by doing exactly that: 100% FPR on normal windows, 100%
   recall on anomalous ones. Checked the other three models too — rule baseline 0.653, isolation
   forest 0.702, two-stage 0.707 — all clustered at or just below that same trivial ceiling.
   Detection F1 comparisons across every model, every round, back to round 1, have been
   uninformative. Fixed: `AVG_WINDOWS_PER_FAULT = 13`, corrected inline comment explaining why.

**Rebuilt the benchmark with both fixes.** Realized anomaly fraction now 18-21.5% (close to the
17.5% target) — but total fault count dropped from 592 to 190 (fewer, correctly-sparse faults now
that each one properly counts for ~13 windows instead of the phantom 4), meaning per-fault-type test
counts are now thinner (5-8 instances/type) than before. Retrained all four models on the corrected
data:

| Model | detection F1 | attribution F1 |
|---|---|---|
| Rule baseline | 0.356 | 0.186 |
| **Isolation Forest ensemble** | **0.428** | **0.421** |
| Two-stage | 0.317 | 0.289 |
| Supervised classifier | 0.290 | 0.227 |

Isolation Forest ensemble remains best, now with bootstrap CI (n=300, fault-instance cluster):
detection F1 mean 0.424 [0.286, 0.562], attribution F1 mean 0.359 [0.244, 0.458]. This CI overlaps
with prior rounds' point estimates (0.297-0.437 across earlier, now-known-broken-density builds) —
**this is not a claim of a dramatically higher number, it's a claim of a trustworthy one for the
first time.** Every number before this round was computed on a benchmark with a station silently
missing from val and an anomaly rate 3x the intended target; this is the first attribution F1 that
isn't contaminated by either bug.

**Supervised classifier, properly diagnosed at last:** under the corrected 18-21% density, FPR on
normal windows is now 1.3% (down from the degenerate 100% under the broken benchmark) but detection
recall collapsed to 17.9% — the model is now under-confident, not over-triggering. This is a genuine
class-imbalance/confidence-calibration problem in the RandomForest (11-way multiclass, `class_weight
="balanced"`, threshold tuned via grid search on val still lands at just 0.25 and still under-fires)
— not yet fixed, and now the real target for the "FPR reconciliation" work queued since round 3.

**Next step:** (a) fix the classifier's recall collapse (threshold/calibration/class-weight tuning,
possibly `predict_proba` recalibration via `CalibratedClassifierCV`), (b) the fault-instance count
dropped to 190 total (down from the artificially-inflated 592) — worth deciding whether to raise
`target_anomaly_frac` above 0.175 now that the fraction-to-fault-count relationship is correctly
calibrated, to get more statistical power without reintroducing the density bug, (c) the
covariance-adjusted attribution idea from earlier in round 6 was tested against the OLD, broken
benchmark — worth a quick re-check against the corrected one before fully discarding it, since the
"loud features carry real signal" theory was itself formed under a benchmark where 55% of the
timeline was fault-affected (which independently could have degraded the rolling 24h baseline's
"clean" reference — see `build_clean_series` — making that diagnosis partially an artifact too).

**(b) tried immediately, same session — negative result, don't repeat.** Diagnosed the classifier's
recall collapse first: best-class probability on anomalous test windows averages 0.162 across 11
classes (barely above the ~0.091 chance level for uniform 11-way guessing), and its argmax is
dominated by just 3 classes (`velocity`/`outtake_velocity`/`temperature` account for 308/379
predictions) regardless of true cause — classic data starvation, not a tunable threshold problem
(the threshold sweep already found the true val optimum at 0.25; every other value is worse).

Tried raising `target_anomaly_frac` 0.175→0.35 to get more fault instances (190→380) and directly
address that starvation. Result: worse across the board — isolation forest attribution F1 dropped
0.421→0.313, and detection F1 crept back toward trivial-baseline territory (rule baseline 0.572,
close to the ~0.57 "always predict anomalous" ceiling at the resulting ~40% realized anomaly rate).
More fault instances via a higher target fraction isn't a free win — the fraction and the
instance count are coupled by construction (more faults at the current average duration necessarily
raises the window-level anomaly rate too), so this just re-introduces a milder version of the same
base-rate distortion round 7 fixed. **Reverted to `target_anomaly_frac=0.175` (the default) — do
not raise it as a way to get more fault instances; that lever doesn't work as hoped.** If more
statistical power is still wanted, the right lever is shortening average fault DURATION (fewer
windows flagged per fault, so more distinct instances fit at the same target rate) — untried.

**Next step, still open, now better-scoped:** given two calibration fixes failed, prioritize
approaches that don't rely on fixing the percentile-race mechanism at all — (a) the supervised
classifier (already trained directly on `causal_parameter` labels, sidesteps the argmax-race
entirely) with its still-queued FPR reconciliation from round 3, or (b) a feature-covariance-aware
attribution score (e.g. rank by deviation from each feature's typical correlation with the others,
not by independent percentile rank) — untried. Fix the station-3-missing-from-val split issue
regardless of which is picked next, since it undermines every threshold-tuning claim made so far.

**Round 6 continued (same session) — tried (b), also regressed. Three-for-three failed fixes now.**
`experiment_covariance_attribution.py`: fit PCA (1-3 components) on train-normal attribution
scores to isolate the shared "everything's elevated together" common-mode factor, then attribute
by the residual after removing it (detection stage left completely untouched — confirmed detection
F1 stayed exactly 0.543 across all three runs). Result: attribution F1 0.176 (k=1), 0.177 (k=2),
0.184 (k=3) — all worse than the 0.331 baseline, and worse than either failed calibration fix from
earlier in this round.

**Status after three independent negative results:** score-recalibration (2 variants) and
common-mode-removal-via-PCA have now all made attribution worse than the original, admittedly-flawed
fixed-percentile-rank baseline. This is no longer "try one more variant" territory — three
differently-designed fixes to the same underlying mechanism (independent per-feature score
comparison feeding an argmax) all failed the same direction. Recommend NOT attempting a fourth
variant of "adjust how the per-feature scores get compared" without a materially different idea.
The strongest untried, structurally different candidate is the supervised classifier — it doesn't
do a score race at all, it's trained directly on `causal_parameter` labels — but its own current
number (0.196) is worse than the unsupervised baseline too, and its FPR reconciliation from round 3
was never actually followed up (only diagnosed). That's the next real candidate, not another patch
to the isolation-forest attribution mechanism.
