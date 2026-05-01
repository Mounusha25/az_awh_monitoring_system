import firebase_admin, csv
from firebase_admin import credentials, firestore
from datetime import datetime

cred = credentials.Certificate('awh-project-460421-52cd6ebf2aa3.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

# Full range: September 1, 2025 to April 7, 2026 end of day
start = datetime(2025, 9, 1, 0, 0)
end   = datetime(2026, 4, 7, 23, 59)

station = 'station_AquaPars #2 @Power Station, Tempe'
query = (db.collection('stations').document(station)
         .collection('readings')
         .where('timestamp', '>=', start)
         .where('timestamp', '<=', end)
         .order_by('timestamp')
         .stream())

rows = []
for doc in query:
    d = doc.to_dict()
    # Convert timestamps to string
    for k, v in d.items():
        if hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
    rows.append(d)
    
if not rows:
    print('No data found for that range')
else:
    keys = sorted(rows[0].keys())
    with open('export.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f'Exported {len(rows)} rows to export.csv')
    # Print first few rows
    for r in rows[:3]:
        print(r)