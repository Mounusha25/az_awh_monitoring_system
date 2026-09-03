# Known Issues & Improvement Backlog

> Captures the open items found during the 2026-08-17 debugging session (dashboard
> downloads/charts appearing broken) and what would actually resolve each one. Not a
> full project backlog — see `PENDING_TASKS.md` for the research-extension (Phase
> 2-5) roadmap. Update this file as items are picked up or closed.

---

## 1. Backend reads Firestore directly — the root performance ceiling

**What:** `awh_az/backend/main.py`'s `/stations/{id}/readings`, `/stations/{id}/hourly`,
and `/export` all read straight from Firestore, which streams documents one at a time.
A single 10,000-row page can take 30-90+ seconds; a genuinely wide/dense date range
(weeks to months on a busy station) can require several such pages sequentially.

**Why it matters:** this is the reason wide-range chart/download requests are slow or
get capped — not a bug in any specific endpoint, a structural property of querying
Firestore this way for range scans.

**Real fix:** migrate `/readings` and `/hourly` to query **Postgres** instead of
Firestore. Postgres already has the full, verified-synced dataset (see §4 below) and
an indexed `WHERE time BETWEEN ... AND ...` range scan returns results in milliseconds
to low seconds *regardless of range width* — no caps, no truncation, no timeouts
needed for any range including full history. This is the only path that makes "give
me the entire selected range, fast" actually true.

- Scope: rewrite the two endpoints' data-fetch logic in `main.py` to use `psycopg2`
  (already a dependency of `ingestion_worker.py`) against the `measurements`/`stations`
  tables instead of the Firestore client. Response shape can stay the same.
- Also worth doing at the same time: confirm whether the `schema_timescaledb.sql`
  hypertable extension is actually enabled on the real Postgres instance (per
  `ingestion_checkpoint_desync_recurrence_2026-08-17` memory, this was **unconfirmed**
  as of 2026-08-17) — a plain table with a btree index on `time` is already fast enough
  for this use case, but a hypertable would be faster still at larger scale.
- Effort: moderate (one focused backend change, well-scoped, no frontend changes
  needed since the response contract doesn't have to change).

**Cheaper partial mitigation (if the Postgres migration isn't done soon):**
parallelize the frontend's page fetches — split a wide date range into several
sub-ranges and fetch them concurrently instead of one page at a time. Since the
backend already offloads Firestore calls to worker threads (see §2), true parallel
I/O should meaningfully cut wall-clock time (plausibly 3-5x). Still fundamentally
bounded by Firestore's total per-document cost for the very widest ranges, and adds
concurrent load to an already-strained free-tier backend — a mitigation, not a fix.

**Status:** closed (2026-08-28) — `/stations/{id}/readings` and `/stations/{id}/hourly`
now query Postgres via a connection pool instead of Firestore; response shape is
unchanged so no frontend changes were needed. Measured: full-history hourly
aggregation for `station_AquaPars@PowerPlant` (~127K readings, 380 days, 1925 hourly
buckets) — **1.7s**, down from an unbounded multi-minute Firestore stream. Deep-offset
`/readings` pagination (offset=100000) — **0.12s**, down from the cursor-skip-and-discard
cost that made deep pages disproportionately slow. Verified field-for-field identical
output against the equivalent live Firestore document before cutting over. Confirmed
along the way: the `timescaledb` extension is NOT installed (plain indexed table) —
per the original note here, that's fine at current scale; hypertable conversion is
still available later if needed. `/export` was deliberately left on Firestore (out of
scope for this fix) and is still slow on wide/unbounded ranges — see the mitigation
above if that becomes the next bottleneck.

**Found and fixed along the way, not originally scoped:** `current` (amperage) was
never in `ingestion_worker.py`'s INSERT statement — Postgres had no `current` column
at all, silently null forever on a field Firestore has real data for (100% of
`station_AquaPars@PowerPlant`'s last 500 readings). Added the column, fixed the
worker's INSERT, restarted it, and backfilled all historical rows from Firestore
(`backfill_current_field.py`, ~3.5 min for 1.5M rows across 9 stations, idempotent/
safe to re-run). Verified `current_mean`/`current_std` populate correctly through the
full `/hourly` pipeline post-migration.

**Regression found and fixed same day, in production:** the migration above assumed
Postgres would be reachable everywhere it's used, but Render's `render.yaml` has no
`DATABASE_URL` at all — production has no network path to the local Postgres instance
`ingestion_worker.py` writes to (it's a `launchd` service on a personal Mac, see #3).
Deployed straight to production, this made `/readings` and `/hourly` hard-503 with
`"PostgreSQL not initialised"` — confirmed live against
`az-awh-monitoring-system.onrender.com`. Fixed by giving both endpoints a Firestore
fallback path (the exact pre-migration logic, kept rather than deleted) used whenever
`db_pool` is unset, mirroring the resilience pattern `cache.py` already uses for Redis.
Verified the fallback directly (forced `db_pool = None`, both endpoints still returned
correct data, `current_mean` included). `/health` now reports Postgres status
separately so this class of gap is visible before a user hits it, not after. Net
effect: production is correct again today (back to Firestore-speed for `/readings`/
`/hourly` until Postgres is reachable from Render); the real fix is still #3 — an
always-on, network-reachable Postgres, not just an always-on worker.

---

## 2. Render free-tier backend: cold starts + limited throughput

**What:** the deployed backend (`az-awh-monitoring-system.onrender.com`) is on
Render's free plan — single instance, shared/limited CPU, spins down after
inactivity (15-50s+ cold-start penalty on the first request back). Combined with
§1, this means backend latency varies a lot session-to-session (observed anywhere
from ~1.7s to 100s+ for what should be comparable requests).

**Fix options, not mutually exclusive:**
- Upgrade off the free tier (removes cold starts, more consistent CPU).
- Once §1 (Postgres migration) is done, this matters much less — Postgres range
  queries are fast enough that even a throttled free-tier CPU should handle them well.

**Status:** open, no action taken. Documented so it's not mistaken for an
application bug if backend latency seems inconsistent again.

---

## 3. `ingestion_worker.py` runs as a `launchd` agent on a personal Mac, not a real server

**What:** per `ingestion_checkpoint_desync_recurrence_2026-08-17` memory, the
ingestion worker was never a deployed service — it had been run manually and left to
die repeatedly, which directly contributed to the ~960K-row desync incident. It's now
installed as a macOS LaunchAgent (`~/Library/LaunchAgents/edu.asu.awh-ingestion.plist`,
`RunAtLoad` + `KeepAlive`), which is a real improvement over "manually started and
forgotten" but **only runs while that specific Mac is powered on and logged in** — not
equivalent to an always-on server. `guides/DEPLOYMENT_GUIDE.md`'s systemd instructions
assume a Linux VM and don't apply to this setup.

**Fix:** move `ingestion_worker.py` to a real always-on host (a small Linux VM with
systemd per the existing deployment guide, or a managed service like Render/Fly/Railway
running it as a background worker) so ingestion doesn't silently stop whenever the
Mac sleeps, reboots, or is offline.

**Status:** open. Worth prioritizing given this exact failure mode (worker silently
not running) is what caused the last major data-integrity incident. **Scope grew
2026-08-28:** it's not just the worker that needs an always-on host — Postgres itself
is also local-only right now (`postgresql://mounusha@localhost:5432/awh_db`), which is
why #1's migration 503'd in production until a Firestore fallback was added. Whatever
solution is picked here needs to leave both the worker *and* a Postgres instance
reachable from Render, not just the worker — a managed Postgres (Render's own, or
Supabase/Neon) that both the worker and the backend can reach is probably simpler than
self-hosting Postgres on the same VM as the worker.

**Concrete impact observed 2026-09-03:** with production Postgres unreachable
(`/health` reports `"postgres":"unavailable (Firestore fallback active)"`), a single
`/stations/{id}/hourly` call for a data-rich station (~130K raw readings) took
28-33s on the Firestore fallback path — confirmed directly against
`az-awh-monitoring-system.onrender.com` (local, Postgres-backed: 0.1s for the same
call). The dashboard's `/compare` page calls this once per station for 5 stations,
and — until the fix below — called it a second time for the chart, so a first visit
could take over a minute before any content appeared, reading as "not loading at
all" rather than "slow." Mitigated same day on the frontend (`compare/page.tsx`):
the chart's default view now reuses the table's already-fetched hourly data instead
of re-fetching the same window, roughly halving first-load time; a "still loading"
notice also appears after 6s so a slow load doesn't look broken. Still fundamentally
bounded by this issue's root cause — the real fix is Postgres reachable from Render.

---

## 4. Ingestion checkpoint/Postgres desync — recovered, but the failure mode can recur

**What:** resolved as of 2026-08-17 (see `ingestion_checkpoint_desync_recurrence_2026-08-17`
memory) — 8/9 stations match Firestore exactly, the 9th is off by 4 rows (normal poll
lag). But this is the **second** occurrence of the same failure mode (first was
2026-07-31), and the root cause — `checkpoint.json` and Postgres row counts being two
independent pieces of state with no reconciliation — hasn't structurally changed.
`check_ingestion_sync.py` (added 2026-08-17) can detect it, but nothing runs it
automatically.

**Fix:** either (a) schedule `check_ingestion_sync.py` to run periodically (e.g. a
daily cron/launchd job) and alert on any nonzero gap, or (b) change the checkpoint
mechanism to something self-verifying (e.g. periodically reconcile against a Firestore
count as part of the worker's own loop) so a future desync is caught automatically
instead of by manual audit.

**Status:** closed (2026-08-28) — `check_ingestion_sync.py` now runs daily via
`~/Library/LaunchAgents/edu.asu.awh-sync-check.plist` and prints an `[ALERT]` line
when the gap exceeds `MISSING_ROW_THRESHOLD` (default 200 rows — comfortably above
the ~4-6 rows of normal poll lag observed on a live check, comfortably below a real
desync like the ~960K-row 2026-08-17 incident).

---

## 5. Redis — resolved by design change, not by provisioning it

**What:** Redis has never been reachable in production (no `REDIS_HOST` ever set on
Render). Rather than provisioning real Redis, `cache.py` now falls back to a bounded
in-process cache (commit `8a4e763`) whenever Redis is unreachable — this works fine
for the current single-instance deployment. See `redis_inprocess_fallback_2026-08-17`
memory for full detail.

**When this would need revisiting:** only if the backend is ever scaled to multiple
instances (in-process cache isn't shared across instances) or needs to survive
redeploys without a cold cache. Not needed for the current setup.

**Status:** closed / not an open issue, listed here for completeness.

---

## 6. CLAUDE.md is out of date in a few specific, confirmed ways

Per the same 2026-08-17 investigation, these are known-stale facts in the project's
own root documentation (`CLAUDE.md`) that should be corrected:

- **9 stations are deployed, not 8** — a 9th, `station_testbed_1@Powerplant` (SRP
  testbed), was added ~2026-07-15 and isn't reflected in the station count.
- **The live backend reads Firestore directly, not Postgres** — `main.py` has no
  `psycopg2`/`DATABASE_URL`/SQL anywhere in it. The pipeline diagram in CLAUDE.md
  (§7, "Data Pipeline — End to End") implies the dashboard is served from
  PostgreSQL/TimescaleDB via the ingestion worker; in reality `ingestion_worker.py`
  feeds a database nothing in the live serving path currently reads from. (This is
  exactly what §1 above proposes fixing — once that's done, the diagram would
  finally be accurate.)
- **Redis section** should note the in-process fallback (§5 above) rather than
  implying Redis caching is fully operational.

**Status:** closed (2026-08-28) — station count corrected to 9, the §7 pipeline
diagram now shows the backend reading Firestore directly (Postgres populated but
not in the serving path), and the Redis section/table notes the in-process fallback
instead of implying Redis is live in prod.

---

## 7. Power-meter `energy` field is inconsistently scaled across stations/time

**What:** found 2026-08-25/26 while fixing negative hourly energy consumption
(`station_AquaPars@PowerPlant` was reporting values like `-64,341 kWh` for a single
hour). Two problems, both in `RPi_USB_Package/read_power.py`:

- **16-bit register overflow.** `read_power.py:80` reads the energy register as a
  single 16-bit word (`response[13:15]`), unlike voltage/current/power just above it,
  which correctly combine a high+low register pair into 32-bit. This wraps at 65,536
  raw Wh (~65.5 kWh) — confirmed directly: `station_AquaPars@PowerPlant`'s raw energy
  reading dropped from `65517` to `1` at 2026-08-25T03:32, with power draw unchanged
  (~1220W) either side. This isn't a meter reset, it's an integer overflow, and it
  recurs roughly every 9-10 days at this station's power draw.
- **Inconsistent units across driver versions.** `read_power.py` uploads raw Wh
  (comment says so explicitly, `/1000.0` conversion added at some point but the Pi
  actually running `station_AquaPars@PowerPlant` appears to predate that conversion —
  live values are Wh-scale, not the small kWh-scale the current script would produce).
  `read_power_new.py` (DEM730P driver, fixed 2026-07-14, confirmed against the meter's
  own LCD: raw=8024 → 80.2 kWh) uploads already-converted kWh. Same field name,
  different units, depending on which station/deploy era produced the reading.

**Current mitigation (`awh_az/backend/main.py`, `/hourly` aggregation):** sums only
positive per-step deltas (never subtracts, so a wrap/reset contributes 0 instead of a
huge negative number), and uses a physical-plausibility heuristic — these stations draw
~1-1.5kW, so a genuine hourly delta over 20 kWh is essentially impossible and gets
treated as raw Wh needing `/1000`; anything under that is left as-is. This is a
best-effort guess per hour, not a real fix — it can't distinguish a true unit from a
station that's uncharacteristically drawing a lot of power for other reasons, and old
pre-fix data collected under the `read_long()` 32-bit-combine bug (see
`read_power_new.py` comment, values in the 140,000-150,000 range observed for
`station_testbed_1@Powerplant` around 2026-07-09/10) remains uncorrected — it's simply
garbage from before the 2026-07-14 fix and isn't recoverable after the fact.

**Real fix:** two parts, both on the Raspberry Pi side —
1. Fix `read_power.py` to read energy as a combined 32-bit register (high+low), the
   same pattern current/power already use, so the counter stops wrapping at ~65.5 kWh.
2. Standardize which unit every deployed station's power-meter driver uploads (pick
   one — kWh matches `read_power_new.py`'s already-fixed, LCD-confirmed behavior — and
   make `read_power.py` match it), then redeploy to every physical station so the
   backend no longer needs to guess.

**Status:** open. Backend-side mitigation shipped 2026-08-26; root cause is still live
on at least `station_AquaPars@PowerPlant`'s deployed Pi script until it's redeployed
with a corrected `read_power.py`.

---

## 8. Hourly water/energy totals silently dropped data across reporting gaps

**What:** `_compute_hourly_aggregation_sync` (`awh_az/backend/main.py`) computed water
and energy as deltas between consecutive readings, but only *within* each clock-hour
bucket — the delta between the last reading of one hour and the first of the next was
never computed at all. Negligible for a normal ~60s gap between readings, but a real
problem after a longer outage: the entire accumulated change during the outage
vanished instead of landing anywhere. Confirmed on `station_testbed_1@Powerplant`:
an 8-day reporting gap (2026-08-03 to 2026-08-11) meant ~98 kWh the meter itself
recorded was completely missing from every hourly total in that window. This affected
`/hourly`'s water and energy figures everywhere they're consumed — the dashboard's
hourly charts, the Live Status widget's period totals, and the Compare page.

A second, related bug surfaced while verifying the fix: `station_testbed_1@Powerplant`
has 2,392 null `energy` readings scattered through its history (4.3% of rows). The
first version of the fix compared only strictly-adjacent readings, so a single null
broke the delta chain and dropped the real change on either side of it — the same
failure mode, triggered by a missing *value* instead of a missing *reading*.

**Fix:** `_compute_hourly_aggregation_sync` now walks the full chronologically-sorted
reading list once (bridging both time gaps and null-value gaps by tracking each
field's last-known-valid value independently) and attributes each delta to the hour
of the reading that closed it, rather than requiring both readings in a pair to share
an hour bucket. A gap's accumulated change lands as a single lump in the reconnection
hour rather than being smoothed across the gap — there's no way to know when within
the gap it happened — but it's no longer silently discarded. The energy Wh-vs-kWh
plausibility check (see #7) now scales its threshold by the elapsed time each bridged
delta actually spans, so a legitimate multi-day accumulation isn't misclassified as
needing the /1000 raw-Wh correction just because it exceeds the old flat per-hour
threshold.

**Verified:** `station_testbed_1@Powerplant`'s full post-fix-driver history
(2026-07-15 onward) — summed hourly energy went from 119.64 kWh (old, boundary-only
fix) to 299.68 kWh (bridging both gap types), against a raw-meter ground truth of
375.05 kWh. The ~75 kWh remaining gap is a single genuine anomaly correctly caught by
the existing plausibility check (an isolated reading pair implying ~604kW average
draw over 8 minutes — physically impossible for this station, correctly treated as a
unit/data glitch and divided down), not a bug. `station_AquaPars@PowerPlant` (zero
gaps, zero nulls) shows unchanged totals — no regression on the common case. All 17
`test_ingestion_worker.py` tests still pass.

**Status:** closed (2026-09-02).

---

## Already fixed this session (for reference — not open items)

- Dashboard download buttons failed silently (no error shown on failure) — fixed,
  now shows a visible error Snackbar.
- Backend blocked its single event loop on wide-range Firestore/aggregation
  requests, freezing the *entire server* for any user during that time — fixed via
  `run_in_threadpool` in `main.py`.
- Charts/raw downloads silently truncated to the first 10,000-row Firestore page,
  showing only a sliver of a wider selected range with no indication anything was
  missing — fixed via pagination (`apiClient.getAllStationReadings`), a visible
  truncation warning when a cap is actually hit, live "N so far" progress instead of
  a static spinner, and a hard per-request timeout so a single stuck request can't
  hang the UI indefinitely.

Full detail on all three: `download_bug_fix_2026-08-17` memory.
