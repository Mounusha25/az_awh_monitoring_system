#!/usr/bin/env python3
"""Monitor the live AWH backend. Exits non-zero (so the CI job fails and GitHub
emails you) when something is wrong.

    python scripts/check_backend.py --mode health      # backend up + Firestore online
    python scripts/check_backend.py --mode freshness   # watched stations still reporting

`health` doubles as a keep-alive: Render's free tier sleeps after ~15 min idle,
and a ping every 10 minutes keeps it awake.

`freshness` reads monitoring/stations.json:
    {"expected_live": {"<station_name>": <max_silent_minutes>, ...}}
Only stations listed there are checked, because most stations are retired and
would alert forever. Add a station when it goes live; remove it when retired.

Stdlib only. Output goes to public workflow logs, so never print secrets
(this script handles none).
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = "https://az-awh-monitoring-system.onrender.com"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "monitoring" / "stations.json"
# Cold start on Render's free tier can take 30-60s.
HEALTH_TIMEOUT = 120
RETRIES = 2


def fetch_json(url: str, timeout: int = HEALTH_TIMEOUT):
    last = None
    for _ in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception as e:  # timeout, DNS, connection reset...
            last = e
    raise RuntimeError(f"{url} unreachable after {RETRIES + 1} attempts: {last}")


def evaluate_health(status: int, body) -> list[str]:
    """Problems with a /health response; empty list means healthy."""
    if status != 200 or not isinstance(body, dict):
        return [f"/health returned HTTP {status}"]
    problems = []
    if body.get("status") != "healthy":
        problems.append(f"status is {body.get('status')!r}, expected 'healthy'")
    fs = (body.get("services") or {}).get("firestore")
    if fs != "online":
        problems.append(f"Firestore is {fs!r}, expected 'online'")
    return problems


def _parse_ts(raw):
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def evaluate_freshness(stations: list[dict], expected_live: dict, now: datetime) -> list[str]:
    """Problems for each watched station that is missing or silent too long."""
    by_name = {s.get("station_name"): s for s in stations}
    problems = []
    for name, max_minutes in expected_live.items():
        s = by_name.get(name)
        if s is None:
            problems.append(f"{name}: not returned by /stations")
            continue
        last = (s.get("metadata") or {}).get("last_reading")
        if not last:
            problems.append(f"{name}: no readings at all")
            continue
        age_min = (now - _parse_ts(last)).total_seconds() / 60
        if age_min > max_minutes:
            problems.append(f"{name}: silent for {age_min:.0f} min (limit {max_minutes}); last reading {last}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["health", "freshness"], required=True)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--config", default=str(CONFIG_PATH))
    args = ap.parse_args(argv)
    base = args.url.rstrip("/")

    try:
        if args.mode == "health":
            status, body = fetch_json(f"{base}/health")
            problems = evaluate_health(status, body)
        else:
            expected = json.loads(Path(args.config).read_text()).get("expected_live", {})
            if not expected:
                print("No stations in expected_live; nothing to check.")
                return 0
            status, body = fetch_json(f"{base}/stations")
            problems = ([f"/stations returned HTTP {status}"] if status != 200
                        else evaluate_freshness(body, expected, datetime.now(timezone.utc)))
    except Exception as e:
        problems = [str(e)]

    if problems:
        print(f"FAIL ({args.mode}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK ({args.mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
