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

**Status:** open. User explicitly chose to leave this as-is for now (2026-08-17) —
current behavior (bounded wait + visible progress + honest truncation warning) is
good enough for typical day/week-scale ranges.

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
not running) is what caused the last major data-integrity incident.

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

**Status:** recovered; detection tooling exists but isn't automated yet.

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

**Status:** open, low-effort — a documentation-only fix once someone has 15 minutes,
or as a side effect of doing §1.

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
