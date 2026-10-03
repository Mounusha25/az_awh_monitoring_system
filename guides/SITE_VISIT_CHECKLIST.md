# Site Visit Checklist: new Pi uploader

Goal: put the durable uploader (`cloud_uploader.py`) on both stations and prove it survives a network outage.
Do the **ASU Pi first**, then the **SRP field testbed**. Everything is reversible (Rollback at the bottom).

Times: Arizona is UTC-7 all year (no daylight saving). The cloud and the checks below use UTC, so
10:00 at the station = 17:00Z.

---

## 0. At your desk, before you leave

- [ ] The function is already deployed (done 2026-10-03). Nothing to do for it.
- [ ] Bring the files. From `RPi_USB_Package/` on your Mac:
      `cloud_uploader.py`, `AquaPars1.py` (ASU), `AquaPars1_new_pm.py` (SRP).
      Put them on a USB stick as a backup in case the Pi can't reach your Mac.
- [ ] Know each Pi's login and address (`pi@<ip>`), or plan to use a keyboard and screen.
- [ ] Optional now, needed only for Part C: nothing. Keys come last.

---

## A. ASU Pi (runs `AquaPars1.py`)

Run commands in a terminal on the Pi, in the folder you normally run from.

1. **Back up the old script**
   ```
   cd ~/RPi_USB_Package
   cp AquaPars1.py AquaPars1.py.bak
   ```
2. **Stop the program** if it is running (click *Stop Acquisition*, or Ctrl+C).
3. **Copy the two files** into `~/RPi_USB_Package/`. From your Mac:
   ```
   scp RPi_USB_Package/cloud_uploader.py RPi_USB_Package/AquaPars1.py pi@<ip>:~/RPi_USB_Package/
   ```
   (or copy from the USB stick).
4. **Sanity checks**
   ```
   python3 -c "import cloud_uploader, requests; print('imports ok')"
   timedatectl show -p NTPSynchronized --value
   ```
   - First line must print `imports ok`.
   - Second must print **`yes`**. If it prints `no`, the Pi's clock isn't synced: check its internet
     connection and fix this before going on. Readings queued while unsynced are dropped on replay by design
     (they stay in the local CSV).
5. **Start it the way you always do**, from the same folder, with or without `sudo` as before. Use the same
   choice every time, or `station_state/` and `logs/` end up owned by different users.
   ```
   cd ~/RPi_USB_Package
   python3 AquaPars1.py
   ```
   Click *Validate Configuration*, then *Start Acquisition*.
6. **Confirm it works** (give it up to 2 minutes). In a second terminal:
   ```
   cd ~/RPi_USB_Package
   tail -f logs/station.log
   ```
   You should see about one line a minute:
   `[Cloud Upload] 200 OK - reading sent (0 still queued)`
   Also check these now exist: `ls station_state/ logs/ measure_data/`
7. **Confirm the cloud got it.** From any machine:
   ```
   curl -s https://az-awh-monitoring-system.onrender.com/stations | python3 -c "import sys,json;[print(s['station_name'],'|',s['status'],'|',s['metadata']['last_reading']) for s in json.load(sys.stdin)]"
   ```
   The ASU station (`station_AquaPars #2 @Power Station, Tempe`) should show `active` and a fresh time.
   (The first request after a quiet period can take up to a minute while the backend wakes.)

### Outage test (the important one)

8. **Write down the time** (note it in UTC). Then cut the network: unplug the Ethernet cable or turn Wi-Fi off.
9. **Wait about 5 to 10 minutes.** While waiting, check:
   - `ls -lth measure_data/` : CSV files keep being written, no gaps. (The old code stalled here.)
   - In the log, **one** line: `upload failing (...); readings are being queued and will be retried`
   - The queue grows:
     ```
     python3 -c "import sqlite3;print(sqlite3.connect('station_state/upload_queue.sqlite3').execute('select count(*) from readings').fetchone()[0])"
     ```
     It should rise by about 1 a minute.
10. **Reconnect the network.** Within 1 to 2 minutes the log should show
    `upload recovered; N reading(s) still queued`, then a run of `[Cloud Upload] 200 OK` lines until the queue
    is back to `0`.
11. **Check the gap was filled with the right times.** Replace the two times with your outage window in UTC:
    ```
    curl -s -X POST https://az-awh-monitoring-system.onrender.com/export -H 'Content-Type: application/json' \
      -d '{"station_names":["station_AquaPars #2 @Power Station, Tempe"],"start_date":"2026-10-XXT17:00:00Z","end_date":"2026-10-XXT17:15:00Z","format":"csv"}' \
      | python3 -c "import sys,csv; rows=[r['timestamp'] for r in csv.DictReader(sys.stdin)]; print(len(rows),'rows'); print('\n'.join(rows))"
    ```
    (The station name has a comma in it, so the CSV must be parsed properly; don't use `cut`.)
    Expect about **one row per minute across the whole outage window**, with timestamps at the time each reading
    was *taken*, not all bunched at the reconnect time. Rows still appear in time order.

### Pass / fail for the ASU Pi
| Result | Meaning |
|---|---|
| `200 OK` lines every minute, station shows `active` | Normal uploads work |
| One `upload failing`, one `upload recovered`, queue back to 0 | Outage handling works |
| Rows for the outage window appear with their original times | Replay works end to end |
| CSV has no holes during the outage | The loop no longer stalls |

**If a check fails: go to Rollback and tell me what the log said.** Don't proceed to the SRP Pi.

---

## B. SRP field testbed (runs `AquaPars1_new_pm.py`)

Repeat A with these differences:

- Use `AquaPars1_new_pm.py` (back up as `AquaPars1_new_pm.py.bak`); the station is `station_testbed_1@Powerplant`.
- **Power, voltage and current are empty by design.** The DEM730P meter only reports *energy*. Blank
  `power`/`voltage`/`current` in the data is normal, not a fault. If energy itself is empty,
  run `python3 test_powermeter_new.py` (or `debug_powermeter.py`).
- **Check the clock sync first** (step A4). A field network can block time servers, and an unsynced Pi
  drops late readings rather than storing a wrong time.
- If the site's network can't be unplugged safely, a shorter outage test is fine: switch the router or
  Wi-Fi off for about 5 minutes.
- Last data from this station was **2026-09-29**, so it has been silent. If it doesn't come back after the
  update, tell me what `logs/station.log` and the console say before changing anything else.

---

## C. Station keys (optional; only after A and B both pass)

Authentication is currently **off**. Don't enforce it until both stations are sending the key.

1. At your desk, make a key (don't paste it into chat or git):
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Save it in your password manager.
2. Cloud Console, Cloud Functions, `receive_data`, Edit, Runtime environment variables:
   add `STATION_KEYS` = `<the key>`. Do **not** add `REQUIRE_STATION_KEY` yet. Deploy.
3. On each Pi:
   ```
   cd ~/RPi_USB_Package
   mkdir -p station_state && printf '%s\n' '<the key>' > station_state/station_key && chmod 600 station_state/station_key
   ```
   Restart the station program. The log's "no station key configured" warning should disappear.
4. After both stations send, search Cloud Logging for `unauthenticated request`. When none appear for a day,
   add `REQUIRE_STATION_KEY` = `true` and redeploy.
5. If a Pi's key is wrong, nothing is lost: readings stay queued and the log says `station key rejected`.
   Fix the key file and restart.

---

## What the log should and shouldn't say

| You see | Meaning |
|---|---|
| `[Cloud Upload] 200 OK - reading sent (0 still queued)` | Good. About 1 a minute |
| `upload failing (...)` once, then `upload recovered` | Outage handled |
| `no station key configured` | Fine while auth is off |
| `dropping reading ... unsynced clock` | Pi clock wasn't synced when it captured that reading; it's still in the CSV |
| `cloud rejected a reading permanently (HTTP 4xx)` | Tell me; a reading was refused as invalid |
| `station key rejected` | Wrong or missing key while the cloud enforces keys |
| `upload queue ... is corrupt` | Rare (power cut on the SD card); a fresh queue starts and the old file is kept |

---

## Rollback (takes a minute)

```
cd ~/RPi_USB_Package
cp AquaPars1.py.bak AquaPars1.py          # (or AquaPars1_new_pm.py.bak -> AquaPars1_new_pm.py)
```
Restart the program. Leave `cloud_uploader.py` and the `station_state/` folder where they are; they're
harmless. The deployed function works with both old and new Pi code, so nothing else needs undoing.

---

## Don'ts
- Don't set `REQUIRE_STATION_KEY=true` before both Pis have keys.
- Don't delete `station_state/upload_queue.sqlite3` while readings are queued; they would be lost.
- Don't paste the station key into chat, git or screenshots.
- Don't skip the outage test; normal uploads working proves very little about it.
