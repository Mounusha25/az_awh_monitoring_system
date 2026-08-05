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
- Week 3–4: LSTM + Isolation Forest models — ✅ built, 14 rounds of iteration, current best LSTM F1=0.415 (see below)
- Week 4–5: LangGraph multi-agent system — ✅ built (`research_extension/phase3_agents/`), see Round 15 below
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

**Round 8 (2026-07-29) — followed up the classifier's FPR reconciliation with `CalibratedClassifierCV`;
also regressed. 4th negative result in this family, first on the classifier side.**
Wrapped `RandomForestClassifier` in `CalibratedClassifierCV(method="sigmoid", cv=5)`
(`supervised_classifier.py`) to address round 7's diagnosis that raw `predict_proba`'s best-class
value averages only ~0.162 across 11 classes (barely above ~0.091 chance) with the argmax dominated
by 3-4 classes (`velocity`/`outtake_velocity`/`temperature`) regardless of true cause. Re-tuned
threshold on val, evaluated on the current (round-7-corrected, 190-fault) test split:

| | detection F1 | attribution F1 | FPR (normal windows) |
|---|---|---|---|
| Raw (round 7 baseline) | 0.291 | 0.214 | 1.5% |
| CalibratedClassifierCV (sigmoid, cv=5) | 0.171 | 0.155 | 1.0% |

Per-fault-type detection recall got worse across the board: drift 31.9%→18.1%, dropout 4.3%→0%,
spike 6.7%→0%, stuck_at stayed at 0%. Mean best-class probability on anomalous test windows actually
rose (0.171→0.764 — calibration made the model *look* more confident) while recall collapsed further
— confirming this is a discrimination problem, not a probability-scale problem: sigmoid calibration
rescales each class's score monotonically but does not change *which* class wins the argmax for a
given sample, and the same handful of "loud" features (`outtake_velocity`, `temperature`, `energy`,
`humidity`) still dominate the predicted-label distribution post-calibration, near-identically to
pre-calibration.

**This is the 4th independently-designed fix in the "adjust the score/probability after the fact"
family to regress performance** (3 on the isolation-forest attribution mechanism in round 6, now this
one on the classifier) — reverted to raw (uncalibrated) as the default in `SupervisedAttributionModel`
(`calibrate=False`), kept as a togglable param for reproducibility only. **Do not attempt isotonic
calibration, per-class calibration, or any other predict_proba rescaling variant on this classifier
without a materially different idea** — the pattern is now consistent across two different model
families and four different rescaling approaches.

**Next real candidates, now better-scoped:** the recurring theme across every failed fix (isolation
forest and classifier alike) is that a small set of features carry a real, elevated, physically-driven
baseline variance that outweighs the true causal feature's signal at the scoring stage, regardless of
how that stage is rescaled after the fact. The two structurally different ideas that haven't been
tried are (a) feature selection/dimensionality reduction *before* the classifier (not a post-hoc score
adjustment — e.g. mutual-information or permutation-importance-based feature pruning per class, so
`velocity`/`temperature`'s dominance can't leak into unrelated classes' decision boundaries), and
(b) LSTM at the attribution stage (still never attempted, queued since round 3). Given four consecutive
regressions on rescaling approaches, (b) — a structurally different model, not another patch to the
current one — is the stronger next bet.

### Round 9 (2026-07-29) — built and tried the LSTM attribution model at last. Also regressed —
worse than every other model, including the already-weak classifier.

Built the infrastructure this needed first, since every existing model only ever consumed windowed
summary stats (`rel_mean`, `rel_std`, ...), never the raw per-timestep readings an LSTM needs:
- `sequence_data_prep.py` — extracts a fixed-length (30 timestep, 2×10-feature) array per window:
  one baseline-relative normalized-value channel and one present/missing mask channel per feature,
  using the exact same rolling per-station/per-feature baseline as `compute_windows` (reused directly,
  not reimplemented) so the LSTM sees the same "deviation from recent normal" signal every other
  model does, just at per-timestep instead of per-window resolution.
- `build_benchmark_dataset.py::build_windows` gained an additive `return_faulted=True` option to also
  hand back each station's raw perturbed dataframe (needed to slice per-timestep sequences) — same
  seed reproduces bit-identical fault placement as the already-saved train/val/test.parquet, so this
  required no changes to the existing benchmark or its labels.
- `build_sequence_cache.py` — one-time script that builds `data/sequence_cache.npz` (14,042 sequences,
  keyed by station_id + window_start) from the above.
- `lstm_attribution_model.py` — a small bidirectional LSTM (concat final-hidden-state from both
  directions → linear classifier over the same 11 classes as the supervised classifier), same
  threshold-gated confidence interface as every other model here so it drops into
  `evaluate.py`/`train_phase2_models.py` unchanged.

**Result: worse than everything, including the classifier it was meant to beat.** Swept 7
configurations (hidden_size ∈ {8,16,24,32}, dropout ∈ {0.3-0.6}, weight_decay ∈ {1e-4,1e-3,1e-2},
epochs ∈ {10,15,20,25,30,40}):

| Model | test detection F1 | test attribution F1 | FPR (normal windows) |
|---|---|---|---|
| Rule baseline | 0.356 | 0.186 | — |
| **Isolation Forest ensemble (current best)** | **0.428** | **0.421** | ~12-29% (varies by round) |
| Two-stage | 0.317 | 0.289 | — |
| Supervised classifier | 0.291 | 0.214-0.227 | 1.5% |
| LSTM (best of 7 configs) | 0.323 | 0.195 | 37.7% |

Every one of the 7 LSTM configs had FPR between 37% and 91% on normal windows regardless of
regularization strength (hidden size 8→32, dropout 0.3→0.6, weight decay 1e-4→1e-2 all tried) — this
rules out "just needs more regularization" as the explanation; it looks like a genuine train→test
generalization gap specific to this model, not a fixable capacity/overfitting problem. Best single
config (epochs=15, hidden=16, dropout=0.5, weight_decay=1e-3): attribution F1 0.195, still below the
classifier's already-weak 0.214-0.227 and far below Isolation Forest's 0.421.

**Reverted to the best-of-sweep config as `LSTMAttributionModel`'s default** (kept as the fitted,
wired-in model in `train_phase2_models.py` since it's a real, honestly-reported result, not hidden) —
this is not a "try again" situation, it's now a genuine data-point: the one structurally different,
never-before-tried model family also underperforms the isolation-forest ensemble on this benchmark.

### Round 10 (2026-07-29) — data forensics audit, prompted by "is any of this a data bug, not a
modeling problem." Answer: yes, a big one. Found real, undocumented hardware failures sitting
inside the benchmark's "clean"/normal reference data, covering **100% of BOTH stations' test
splits** and most of station 6's val split too. Every F1 number in rounds 1-9 was measured partly
against this.

Every prior round (1-9) treated any non-injected real reading as trustworthy "normal" ground truth.
That assumption is false for large, contiguous stretches of the actual deployment:

1. **Station 3: `humidity` frozen at exactly 100.0 (std=0.0) for every reading from 2026-07-09
   onward** — the sensor resumes after a ~7-week data gap already stuck. Same onset date, the
   entire **outtake anemometer channel** (`outtake_temperature`, `outtake_humidity`,
   `outtake_velocity` — physically co-located on one USB unit) also goes dead/constant. This is
   **100% of station 3's test split** (all of it falls after 07-09) and **25.4% of its val split**.
2. **Station 6: `power`, `energy`, AND `voltage` all frozen at exactly 0.0 together for
   2026-06-10 through (at least) 06-26** — consistent with the power meter/RS485 link failing or
   the station losing power outright, not three independent coincidental sensor faults. This covers
   **100% of station 6's val split, 100% of its test split, and 25.5% of its train split.**
3. **Station 6: `velocity` frozen at exactly 0.0 for 2026-05-05 through 05-19** (15 days, inside
   train), followed by a week (05-25 to 06-01) where real velocity averages 85.97 (up to 220.72) —
   30-70x its normal ~1-3 range. Both sit inside train, meaning every model has been learning a
   "normal" velocity baseline for station 6 that alternates between dead-zero and a wildly
   corrupted outlier week before ever seeing genuinely typical readings.
4. Smaller: station 3 `weight` frozen at exactly 1315.00 for one day (2026-05-08), inside train.

**Mechanism — a real pipeline bug, not just bad luck:** `compute_windows`'s
`safe_std = baseline_std if baseline_std and baseline_std > 0 else np.nan` silently produces NaN
(later filled with a fixed train-set mean by `data_prep.py::prepare_columns`) whenever a feature's
rolling "clean" baseline has collapsed to zero variance — which is exactly what a dead/frozen sensor
does. Confirmed via `fault_log.csv`: **62 of station 6's 122 injected faults (51%) start during the
power/energy/voltage-dead window**, 12 of them injected directly onto `power`/`energy`/`voltage`
themselves — meaning those 12 faults' signal is masked by a fixed fallback constant regardless of
injected magnitude, not scoreable by design. Station 3 has 11/68 faults (16%) landing in its dead
window, including 3 onto `outtake_velocity` (itself dead at the time).

**What this means for every F1 number reported so far:** the reigning best (Isolation Forest,
attribution F1 0.421) has never been measured against a test period free of these real failures —
both stations' entire test splits are affected. This doesn't mean 0.421 is wrong, but it means it's
an unknown mix of "genuine synthetic-fault attribution skill" and "how the pipeline behaves when a
feature's clean baseline is dead," and there's no way to separate those from the numbers alone. The
true achievable ceiling on genuinely clean data could be meaningfully higher OR the models could be
silently benefiting from some fault types being easier when co-occurring features are inert — not
knowable without re-running the benchmark on cleaned data.

**Distinct from, but related to, [[dataset_station_coverage_gap]]:** that memory already flagged
"only 2 of 8 stations have real volume." This finding sharpens it further — even within those 2
stations, real *dense, continuously-representative* data is only ~15-18 real days/month clustered in
a ~10-13 week window (station 3: 22 total distinct days with data, nearly all in May+July 2026;
station 6: 38 distinct days, nearly all in May-June 2026), not spread across the nominal "11-month
deployment" CLAUDE.md describes. Because the per-station quantile split (round 7's fix) sizes cuts
by window COUNT and the two stations' real density differs week to week, their resulting calendar
test windows barely overlap at all (station 6's test ends 07-09; station 3's test starts 07-10) —
"pooled test F1" silently blends two non-contemporaneous eras, one of which (per finding #1 above)
is also mid-hardware-failure.

### Round 11 (2026-07-29, same day) — implemented the per-feature dead-period exclusion fix and
re-ran all 5 models. It fixed the mechanism, but exposed a deeper problem the fix cannot solve:
100% of both stations' test windows still sit inside a real hardware failure, and there isn't
enough genuinely-clean calendar time at either station to build a split that avoids this.

**What was implemented:** `build_benchmark_dataset.py::detect_dead_periods()` flags any
(station, feature) day where raw std < 1e-6 with >= 20 readings, merges into contiguous ranges
(written to `data/dead_periods.csv`, 12 periods found — matches Round 10's manual findings exactly).
These are now (a) excluded from `inject_faults`' placement search per-column (`inject_synthetic_faults.py`
gained a `dead_periods` param), so no synthetic fault lands on an already-dead feature, and
(b) excluded from the rolling "clean" baseline in `compute_windows` and in the LSTM's
`build_clean_series_for_station` (both now receive `fault_log + dead_periods` as the exclusion set).
Rebuilt the benchmark and `sequence_cache.npz` and reran `train_phase2_models.py`:

| Model | test detection F1 (before → after) | test attribution F1 (before → after) |
|---|---|---|
| Rule baseline | 0.356 → 0.378 | 0.186 → **0.307** |
| Isolation Forest ensemble (was reigning best) | 0.428 → 0.270 | 0.421 → 0.239 |
| Two-stage | 0.317 → 0.246 | 0.289 → 0.209 |
| Supervised classifier | 0.291 → 0.272 | 0.214-0.227 → 0.212 |
| LSTM | 0.323 → 0.204 | 0.195 → 0.015 |

**The rule baseline now beats every "sophisticated" model** — a real inversion of the whole
project's premise so far. Isolation Forest's FPR on normal windows jumped to 50.5% (previously
12-29% across rounds).

**Root cause of the FPR jump — the fix corrected the bug but didn't (and structurally can't) fix
the split:** dead-period windows are still real windows inside the test split, and they still get
scored — nothing in a per-feature fix removes them from evaluation, since 100% of both stations'
test splits sit chronologically inside their own dead period (station 3 test: 07-10 to 07-13, humidity
dead from 07-10 onward; station 6 test: 06-21 to 07-09, power/energy/voltage dead 06-10 to 06-27+).
Before the fix, the rolling "clean" baseline itself drifted toward the frozen value the longer a dead
period went on (since post-freeze frozen readings were incorrectly feeding back into their own
baseline as "clean"), which — accidentally — made later windows in a dead stretch look progressively
*less* anomalous over time, masking the problem. The fix anchors the baseline to genuine pre-freeze
history and keeps it there for the entire dead period, so now every dead-period window scores as
persistently, maximally deviant from healthy history — which is arguably *correct* behavior (it IS
a real anomaly) but counts as a false positive under this benchmark's definition (`is_anomaly` is
only ever true for synthetic faults). **Fixing the bug didn't hide the underlying problem, it
surfaced it more clearly.**

**Checked how much calendar time is genuinely clean (all 10 features simultaneously functioning) at
either station, to see if the split itself could just be moved to avoid this:** not enough to matter.
Station 3: real functioning data only exists 05-09 to 05-15 (~7 days, before the outtake-channel
blip) and 07-06 to 07-09 (~4 days, right before the humidity freeze) — ~11 days total, in two
disconnected fragments weeks apart (there's also a ~7-week gap with literally zero rows in between,
May 20 – July 6, unrelated to the freeze). Station 6: 05-20 to 06-09 (~3 weeks) is the only stretch
free of both the velocity-dead period (ends 05-19) and the power/energy/voltage-dead period (starts
06-10) — but that same stretch contains the still-undiagnosed real velocity outlier week (05-25 to
06-01, values 30-70x normal, a different kind of contamination entirely, not caught by the dead-period
detector since its std is abnormally *high*, not zero). Excluding that too leaves two further
fragments of ~5 and ~9 days. **Neither station has enough genuinely-clean, simultaneously-functioning
calendar time to support a properly-powered train/val/test split on its own** — this is not a
pipeline bug, it's a real limitation of how little uninterrupted clean operation these 2 stations
have actually had.

**Where this leaves RQ1 feasibility, stated plainly:** the modeling side (5 architectures across 11
rounds: rescaling, calibration, LSTM, and now a corrected benchmark) is very unlikely to be the
bottleneck. The bottleneck is that this pilot deployment has not yet produced enough continuous,
simultaneously-healthy real sensor data to support the kind of held-out time-based generalization
claim the F1 > 0.80 target implicitly assumes. Getting past this needs one of: (a) the physical
stations running reliably, without the kind of week+-long hardware dropouts seen here, for a longer
continuous stretch before the next benchmark build, or (b) an explicitly-scoped, smaller evaluation
(e.g. a purely synthetic time series not tied to these 2 stations' real gaps) with the limitation
stated up front, or (c) treating the current numbers as a lower bound and revisiting once more clean
real data exists. Model architecture changes alone will not move this.

**Update — option (a) implemented in Round 11, see below.** Options (b) (restrict calendar range to
all-features-clean periods) and (c) (flag dead-period windows as not-evaluable rather than scoring
them) are still on the table — Round 11 found (a) alone isn't enough, since 100% of both stations'
test windows sit inside a real failure regardless of per-feature exclusion.

### Round 12 (2026-07-31) — the "only 2 stations have real data" premise behind rounds 1-11 was
itself a stale-database artifact. Caught the ingestion worker up on ~2 weeks of missed Firestore
data and discovered 6 more stations have substantial, previously-invisible real volume. Rebuilt the
benchmark across all 8 usable stations — LSTM (the worst model in every prior round) is now the
best, but every model's absolute F1 dropped, which is a harder-but-more-honest result, not a
regression.

**What happened:** while investigating why the local Postgres DB looked so thin, found (a) the
ingestion worker's local checkpoint had been reset and hadn't run in ~2 weeks, and (b) the
`FIREBASE_CREDENTIALS_PATH` used in ad-hoc debugging pointed at a dead/wrong Firebase project
(`azawh-754de`, no Firestore database provisioned at all) instead of the one the app actually uses
by default (`awh-project-460421`). Ran a full catch-up against the correct project, seeded from each
station's current Postgres max timestamp: **473,864 new rows inserted.** This revealed that stations
1, 2, 4, and 7 — previously assumed to be "lightly-used test/dev units with 6-44 windows each" (see
[[dataset_station_coverage_gap]], now superseded) — actually have 11K-305K rows each of clean, dense
real data that had simply never been ingested locally. Station 9 is a newly-active station,
currently live (still streaming as of this writing). Only station 8 (4 rows total) remains
negligible. Station 5, previously unknown, has a real ~85-day span (one stretch 29 days
continuous) but real intake-anemometer flakiness for several multi-week stretches in Feb-Apr 2026 —
correctly handled by the round-10/11 dead-period exclusion fix, no special-casing needed.

**Rebuilt the benchmark with `INCLUDED_STATIONS = [1, 2, 3, 4, 5, 6, 7, 9]`** (was `[3, 6]`):
551,472 raw rows (was 69,186), 49,075 labeled windows (was 14,042), **656 total fault instances (was
190 — a 3.4x increase directly targeting the data-starvation diagnosis from rounds 3/8/9)**. Reran
all 5 models:

| Model | 2-station (Round 11) | 8-station (Round 12) |
|---|---|---|
| Rule baseline | 0.307 | 0.110 |
| Isolation Forest | 0.239 | 0.162 |
| Two-stage | 0.209 | 0.137 |
| Supervised classifier | 0.212 | 0.172 |
| **LSTM** | 0.015 | **0.224 (now best)** |

**Every model's absolute F1 dropped, but this is a harder, more representative evaluation, not a
worse one** — pooling across 8 real stations with genuinely different hardware personalities and
operating regimes is intrinsically harder than 2, and per-fault-type test counts are now large
enough to trust (20-33 instances/type, vs 4-8 before): drift 511 windows/20 faults, dropout
166/23, spike 193/33, stuck_at 295/26. **The LSTM going from worst (0.015-0.195 across rounds 1-9)
to best (0.224) on the exact same architecture is the clearest confirmation yet of the round-9
theory** that it was specifically data-starved, not fundamentally unsuited to this problem — more
independent fault instances per (fault_type, feature) combination is precisely what a from-scratch
sequence model needs and previously lacked.

**Still nowhere near the RQ1 target (>0.80)**, and this doesn't change that conclusion — but it now
rests on a real 8-station, 656-fault sample instead of a thin, partly-broken 2-station one. Next
step, now well-motivated: revisit whether the LSTM specifically benefits further from feature
selection / a slightly larger sequence model now that data-starvation is less severe, and re-run the
Round 5 rotating-k-fold stability check on this larger benchmark (the old fold-to-fold fragility
finding may no longer hold with ~3x the fault instances).

**Operational note:** the ingestion worker's local checkpoint (`~/.awh-ingestion/checkpoint.json`)
should be kept running (or re-run periodically) going forward — it was silently stale for weeks
before this was caught, and the ad-hoc debugging session that surfaced this also found a *second*,
unrelated stale/dead Firebase project key file (`azawh-754de`) sitting in `~/Downloads` that should
not be confused with the real one going forward.

### Round 13 (2026-07-31, same day) — scoped `FEATURE_COLUMNS` down from all 10 sensor channels to
just the 4 the lab actually cares about (temperature, humidity, weight, power). Biggest single
improvement of the entire project — every model roughly doubled or better.

**Motivation:** every round back to round 6 found the same recurring root cause for low attribution
accuracy — a subset of "loud" features (`velocity`, `voltage`, `outtake_*`) have larger natural
variance and kept winning the cross-feature comparison regardless of true cause. Those channels
aren't ones the lab uses; only temperature/humidity/weight/power are. A quick post-hoc check (before
committing to a rebuild) confirmed the idea had merit: restricting the trained models' final
argmax to just those 4 columns (without retraining) lifted LSTM attribution F1 0.224→0.279 and
classifier 0.172→0.254, while leaving Isolation Forest roughly flat (0.162→0.184) — expected, since
its per-feature forests are trained fully independently and don't care how many *other* forests
exist.

**Given that signal, rebuilt properly** — set `FEATURE_COLUMNS = ["temperature", "humidity",
"weight", "power"]` in `build_benchmark_dataset.py` (single source of truth, propagates through
`compute_windows`, `data_prep.py`, `inject_synthetic_faults.py`, `sequence_data_prep.py`, and
`lstm_attribution_model.py`'s class vocabulary with no other code changes needed). Same 8 stations,
same total fault budget (~650 instances) — but now concentrated across 4 candidate features instead
of 10 (~160/feature instead of ~65/feature), and the noisy 6 columns are gone from the input
entirely, not just unavailable as an output label.

| Model | 8-station, 10-feature (round 12) | 8-station, 4-feature (round 13) |
|---|---|---|
| Rule baseline | 0.110 | 0.227 |
| Isolation Forest | 0.162 | **0.356** |
| Two-stage | 0.137 | 0.315 |
| Supervised classifier | 0.172 | 0.369 |
| **LSTM** | 0.224 | **0.380 (best)** |

**The Isolation Forest result (0.162→0.356) is bigger than the post-hoc check predicted (0.184),
and the reason clarifies something the post-hoc check couldn't isolate: detection, not just
attribution, benefits from dropping the noisy columns.** The post-hoc test only restricted the
*attribution* argmax; *detection* was still `max()` over all 10 features' scores, so a noisy
feature's natural spike could still trigger a false detection independent of attribution. With the
noisy columns gone entirely, detection F1 jumped too (Isolation Forest: 0.334→0.502, LSTM:
0.292→0.490, classifier: 0.382→0.554) — cleaner detection cascades into cleaner attribution
downstream, for every model, not just the joint ones. Per-fault-type (new best, LSTM): drift
recall 59%/attribution-given-detected 68%, dropout 62%/68%, spike 58%/54%, stuck_at 38%/32% (still
the hardest fault type, consistent with every prior round).

**This is the best result in the whole project (0.380), on the most honest benchmark yet** (8 real
stations, dead-sensor contamination fixed, faults concentrated on the features that matter) — but
still well below the RQ1 target of 0.80. Next candidates, now better-motivated given how much
scoping the feature space just helped: (a) the ensemble idea (Isolation Forest for detection + LSTM
for attribution) queued after round 12, now worth retrying on this cleaner 4-feature benchmark;
(b) re-sweep LSTM hyperparameters again — round 9's sweep was tuned for a data-starved 190-fault,
10-feature regime, now twice-obsolete; (c) stuck_at attribution specifically remains the weak point
across every model and warrants its own targeted look (a frozen value's shape may just be less
distinctive among only 4 candidate features than the loud-feature-dominated setting where it was
merely "collapsed to 0%" — worth checking whether the confusion is concentrated among the 4 target
features or scattered).

### Round 14 (2026-07-31, same day) — tried the two queued leads from round 13 ("keep squeezing").
One negative result (new feature), one confirmed positive (LSTM re-sweep) — new best is 0.415.

**Attempt 1 (negative): "prior constant hours" feature, targeting the power/weight stuck_at
confound.** Round 13 found power/weight stuck_at detection recall very low (19%/5%) and hypothesized
it's because real idle periods (pump off, nothing being collected) are themselves long constant
stretches that look just like a frozen sensor from *inside* a window alone. Added
`{feature}_prior_constant_hours` (`build_benchmark_dataset.py::run_start_indices` +
`prior_constant_hours`, capped at 6h) — how long the reading right before the window started had
already been constant, on the theory that a genuine fault's onset is independent of any real
operational transition (short prior run) vs. a window sitting well inside an already-long-idle
stretch (long prior run). **Result: flat to slightly negative** (Isolation Forest 0.356→0.348,
two-stage 0.315→0.305, classifier 0.369→0.358) and the specific target metric didn't move at all —
power stuck_at recall unchanged (19.2%→19.2%), weight got worse (5.2%→0.9%). Same "too many
dimensions for one forest" pattern as every previous feature-addition attempt that didn't pan out.
**Reverted** (removed from `DETECTION_STAT_SUFFIXES` and `compute_windows`, not kept as a togglable
option — clean revert, no dead code left in the pipeline).

**Attempt 2 (positive, confirmed): re-swept the LSTM's hyperparameters on the round-13 benchmark.**
Its defaults (`epochs=15, hidden=16, dropout=0.5, wd=1e-3`) were tuned in round 9 against a very
different, much more data-starved regime (190 faults spread across 10 features, ~65/feature). Round
13's benchmark has ~160 fault instances per feature now (4 features instead of 10, same total
budget) — different enough that the old optimum was worth re-checking. Tried 3 configs; the best
(`epochs=30, hidden=24, dropout=0.4, wd=5e-4` — more capacity, less aggressive regularization) beat
the default (0.380) on the first run: **0.415**. Verified it wasn't a lucky seed — reran across 4
more seeds/nearby variants: 0.389, 0.402, 0.380, 0.389, all at or above the old default, mean ~0.39.
**Adopted as the new default** in `lstm_attribution_model.py`. Official run:

| Model | Round 13 | Round 14 |
|---|---|---|
| Rule baseline | 0.227 | 0.227 |
| Isolation Forest | 0.356 | 0.356 |
| Two-stage | 0.315 | 0.315 |
| Supervised classifier | 0.369 | 0.369 |
| **LSTM** | 0.380 | **0.415 (new best)** |

**Current best result in the project: LSTM, attribution F1 = 0.415**, on the round-13 benchmark (8
real stations, dead-sensor contamination fixed, scoped to temperature/humidity/weight/power). Still
well below the RQ1 target (>0.80). The power/weight stuck_at confound from round 13 remains
unresolved — one genuinely-motivated attempt at it failed; it may need a structurally different idea
(not another engineered feature) or may be a real, hard-to-remove limitation of synthetic stuck_at
injection on features with natural idle periods.

### Round 15 (2026-07-31, same day) — built Phase 3, the LangGraph multi-agent system, per CLAUDE.md
Section 9's proposal. Decided to treat Phase 2 as settled (0.415, well-earned after 14 rounds) rather
than keep pushing, and move to the next phase in the timeline.

Lives in `research_extension/phase3_agents/`. Orchestrates the Phase 2 Isolation Forest ensemble
(not the LSTM — the LSTM needs a raw-sequence cache lookup keyed to precomputed windows, which
doesn't fit an agent receiving one arbitrary window; Isolation Forest only needs the window's
already-available `rel_*` stat columns) rather than replacing it. Two genuine unknowns were resolved
by explicit user decision before implementing, not invented: (a) "EPA regulatory thresholds" per the
original proposal don't map cleanly onto AWH telemetry (temperature/humidity/weight/power aren't
EPA-regulated contaminants) — implemented as clearly-labeled placeholder operational sanity bounds
instead, not real regulatory data; (b) stakeholder escalation routing is simulated/logged only, no
real notification channel (Slack/email/etc.) wired up.

**Architecture:** `StateGraph` with `SensorDriftAgent` and `ThresholdBreachAgent` as parallel
branches from START, fanning into a `merge` node that decides `needs_incident` — most windows are
normal, so the conditional edge skips `IncidentReportAgent` (the LLM call) and
`StakeholderEscalationAgent` entirely rather than reaching them on every window, matching how a real
monitoring system would behave (silence is the common case). `IncidentReportAgent` retrieves
grounding context via simple keyword-overlap search over `guides/*.md` (`rag_corpus.py` — not a
vector DB; the ~3K-line corpus doesn't need one) and calls Claude (`claude-opus-5`, adaptive
thinking) to translate the statistical finding into a plain-language summary for a non-specialist
field engineer — the RQ3 target from CLAUDE.md.

**Verified without needing API credentials:** ran the full graph on a genuine normal window from the
Phase 2 test split — correctly terminated at `merge` with no `incident_report`/`escalation` keys in
the output, confirming the conditional early-exit works. Ran it against 15 real anomalous windows —
found one where `SensorDriftAgent` actually flagged something (the others were false negatives,
consistent with Isolation Forest's ~50% detection recall) and confirmed it correctly reached
`IncidentReportAgent` and failed **only** on the expected `TypeError: Could not resolve authentication
method` — proving every non-LLM part of the pipeline (both parallel agents, the merge/routing logic,
RAG retrieval, state threading) is wired correctly. **Not yet run end-to-end with a real LLM call** —
needs `ANTHROPIC_API_KEY` (or `ant auth login`, neither present in this environment) before the
`IncidentReportAgent`/`StakeholderEscalationAgent` nodes can actually execute.

**Next step:** once credentials are available, run `python run_pipeline.py` (needs
`research_extension/phase1_streaming/venv_phase1`, `langgraph`+`anthropic` already installed there)
against real sampled windows plus the synthetic out-of-bounds case it includes, and sanity-check the
generated incident report quality — no human read-through of an actual LLM-generated report has
happened yet.

**Why this probably happened, and what it means for what's left to try:** an LSTM needs many
*independent* sequences of each fault's temporal shape to learn a generalizable shape detector; this
benchmark has only ~190 independent fault instances total across 4 fault types and 10 possible
features (round 7's corrected count) — meaning as few as ~5 independent examples of a specific
(fault_type, feature) combination's temporal shape, nowhere near enough for a from-scratch sequence
model, even a small one. This is consistent with — not contradicting — the round 7/8 conclusion that
the classifier's problem was data starvation too. **The isolation-forest ensemble's structural
advantage is now clearer in hindsight: it doesn't need per-(fault_type, feature) examples to learn a
shape at all** — it only needs enough *normal* data per feature to establish "what's typical," which
this dataset has in abundance (11,500+ normal windows). Any future from-scratch supervised sequence
or classifier approach will hit the same instance-count ceiling; the more promising open directions are
now models that lean on the abundant normal data rather than the sparse fault instances — e.g.
per-feature autoencoders/reconstruction-error scoring (trained only on normal data, no fault-instance
count problem at all), or the still-untried feature-selection idea from round 8 applied ahead of the
classifier.
