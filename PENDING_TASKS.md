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
Per CLAUDE.md Section 9 — not started yet.
- Week 1–2: Kafka + Spark streaming layer
- Week 2: Benchmark dataset (labeled anomalies, train/val/test split)
- Week 3–4: LSTM + Isolation Forest models
- Week 4–5: LangGraph multi-agent system
- Week 6–7: Evidently AI + Airflow MLOps pipeline
- Week 8: Kubernetes + Grafana deployment
