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
