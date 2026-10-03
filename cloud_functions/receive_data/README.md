# receive_data Cloud Function

Station (Raspberry Pi) -> Firestore ingest endpoint. Project `awh-project-460421`,
function `receive_data` (us-central1). Endpoint:
`https://us-central1-awh-project-460421.cloudfunctions.net/receive_data`

See the docstring in `main.py` for the payload and the optional `reading_id`,
`replayed`, `client_timestamp` fields. The change is backward compatible: stations
that send none of them behave exactly as before.

## Tests
    pip install -r requirements.txt pytest
    pytest cloud_functions            # run from the repo root, in its own pytest run
(`main.py` shares its name with the backend's `main.py`, so don't run both in one session.)

## Deploy (manual, needs an account with Cloud Functions access, e.g. azawh.asu@gmail.com)
Console: Cloud Functions -> receive_data -> Edit -> Source: paste `main.py` and
`requirements.txt` -> Deploy. Keep entry point `receive_data`.

Or with gcloud (check the existing runtime/generation first with `gcloud functions describe receive_data`):
    gcloud functions deploy receive_data --project awh-project-460421 --region us-central1 \
      --runtime python312 --trigger-http --allow-unauthenticated --entry-point receive_data \
      --source cloud_functions/receive_data
Match the runtime and `--gen2` flag to what is deployed today; do not change the trigger/auth settings.

## Verify after deploy (no test data written to a real station)
1. Legacy still works: the existing stations keep appearing in the dashboard with fresh readings.
2. Bad input now returns 4xx instead of 500:
       curl -s -o /dev/null -w "%{http_code}\n" -X POST -H 'Content-Type: application/json' -d '{}' $URL   # expect 400

## Authentication (shared key, X-Station-Key header)
The function was public: anyone with the URL could write readings for any station. It now
supports a shared key. It is deployed in **soft mode** first so stations that have not been
updated yet keep working.

| Env var on the function | Meaning |
|---|---|
| `STATION_KEYS` | Comma-separated valid keys. Several keys at once = rotation without downtime. |
| `REQUIRE_STATION_KEY` | `true` = enforce. Anything else = soft mode. |

Soft mode: no key -> accepted, but logged as `unauthenticated request ... from station '<name>'`;
wrong key -> rejected (401). Enforce mode: missing or wrong key -> 401. Enforce with no
`STATION_KEYS` rejects everything (fails closed). Keys are never logged.

### Rollout
1. Generate a key (keep it out of git and chat history):
       python3 -c "import secrets; print(secrets.token_urlsafe(32))"
2. Deploy the function with `STATION_KEYS=<key>` and **no** `REQUIRE_STATION_KEY`.
   Console: Cloud Functions -> receive_data -> Edit -> Runtime, build... -> Runtime environment
   variables. (Or `gcloud functions deploy ... --update-env-vars STATION_KEYS=<key>`.)
   Existing stations are unaffected.
3. On each Pi, put the key where the uploader finds it, then restart the station program:
       mkdir -p station_state && printf '%s\n' '<key>' > station_state/station_key && chmod 600 station_state/station_key
   (or set the `AWH_STATION_KEY` environment variable; it takes precedence).
4. Wait until nothing is unauthenticated. In Cloud Logging search for
   `unauthenticated request`; every station name still appearing needs step 3.
5. Set `REQUIRE_STATION_KEY=true` and redeploy. Verify an unkeyed request now gets 401:
       curl -s -o /dev/null -w "%{http_code}\n" -X POST -H 'Content-Type: application/json' -d '{"station_name":"x"}' $URL   # 401

If a Pi's key is wrong or missing under enforcement, its readings are NOT lost: the uploader
keeps them queued (HTTP 401/403 is retried) and the local CSV continues. Fix the key and the
backlog uploads with the right timestamps.

### Rotating a key
Set `STATION_KEYS=<old>,<new>`, update the Pis to `<new>`, confirm no station still uses the old
one, then remove `<old>`. Rotate immediately if a key is ever pasted somewhere public.

### Limits
A shared key stops casual or accidental writes; it does not stop someone who extracts the key from
a Pi. Per-station keys or Secret Manager (instead of a plain env var) would be the next step.
Also rejected now regardless of auth: station names containing `/` (which Firestore treats as a
path), reserved/over-long/control-character names, and bodies over 64 KB.
