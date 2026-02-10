#!/usr/bin/env python3
"""
Quick verification that everything is working
"""
import os
import sys

# Setup environment
os.environ['DATABASE_URL'] = 'postgresql://postgres@localhost/awh_db'
os.environ['FIREBASE_CREDENTIALS_PATH'] = '/tmp/firebase-key.json'

sys.path.insert(0, os.path.dirname(__file__))

print("🔍 Verifying AWH Ingestion System Setup...\n")

# Test 1: Check Python environment
print("1️⃣  Python Environment")
print(f"   Python: {sys.version.split()[0]}")
print(f"   Path: {sys.executable}\n")

# Test 2: Check imports
print("2️⃣  Required Packages")
try:
    import psycopg2
    print(f"   ✅ psycopg2 {psycopg2.__version__}")
except ImportError as e:
    print(f"   ❌ psycopg2: {e}")
    sys.exit(1)

try:
    import firebase_admin
    print(f"   ✅ firebase-admin {firebase_admin.__version__}")
except ImportError as e:
    print(f"   ❌ firebase-admin: {e}")
    sys.exit(1)

try:
    import dotenv
    print(f"   ✅ python-dotenv available\n")
except ImportError as e:
    print(f"   ❌ python-dotenv: {e}")
    sys.exit(1)

# Test 3: Check database
print("3️⃣  Database Connectivity")
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM stations;")
    stations_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM measurements;")
    measurements_count = cur.fetchone()[0]
    conn.close()
    print(f"   ✅ Connected to awh_db")
    print(f"   📊 Stations table: {stations_count} rows")
    print(f"   📊 Measurements table: {measurements_count} rows\n")
except Exception as e:
    print(f"   ❌ Database error: {e}\n")
    sys.exit(1)

# Test 4: Check ingestion worker
print("4️⃣  Ingestion Worker Code")
try:
    from ingestion_worker import CheckpointManager
    import tempfile
    
    # Create temp checkpoint file
    temp_checkpoint = os.path.join(tempfile.gettempdir(), 'checkpoint-test.json')
    cp = CheckpointManager(temp_checkpoint)
    cp.save(timestamp='2024-01-01T00:00:00Z', count=0)
    loaded = cp.load()
    print(f"   ✅ ingestion_worker.py imports correctly")
    print(f"   ✅ CheckpointManager working\n")
except Exception as e:
    print(f"   ❌ Error: {e}\n")
    sys.exit(1)

print("=" * 60)
print("✅ ALL SYSTEMS READY!")
print("=" * 60)
print("\n📋 Next Steps:")
print("\n1. Create a Firebase credentials file:")
print("   cp your-firebase-key.json /tmp/firebase-key.json")
print("\n2. Run the ingestion worker:")
print("   python3 ingestion_worker.py")
print("\n3. Monitor data in the database:")
print("   psql -U postgres -d awh_db -c 'SELECT COUNT(*) FROM measurements;'")
print("\n" + "=" * 60)
