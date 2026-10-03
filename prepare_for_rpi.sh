#!/bin/bash
# Build a clean package of the files a station needs, from RPi_USB_Package/.
# Run from the repository root:  ./prepare_for_rpi.sh
#
# Both AquaPars scripts import cloud_uploader.py, so it MUST be in the package;
# the script stops with an error if any listed file is missing.
set -euo pipefail

SRC="RPi_USB_Package"
OUTPUT_DIR="AWH_RPi_Package"

FILES=(
  AquaPars1.py            # ASU station (older Prolific power meter)
  AquaPars1_new_pm.py     # SRP field testbed (DEM730P power meter)
  cloud_uploader.py       # durable non-blocking uploader (required by both)
  awh_ui_layout.py
  pump_controller.py
  read_balance.py
  read_power.py
  read_power_new.py
  read_flow.py
  intake_anemometer.py
  outtake_anemometer.py
  RASPBERRY_PI_COMMANDS.txt
)

[ -d "$SRC" ] || { echo "ERROR: run this from the repository root (no $SRC/ here)"; exit 1; }

echo "Creating RPi package..."
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

for f in "${FILES[@]}"; do
  [ -f "$SRC/$f" ] || { echo "ERROR: missing $SRC/$f"; exit 1; }
  cp "$SRC/$f" "$OUTPUT_DIR/"
done

# Sensor test scripts (flat layout, no test_system/ folder). Not the pytest unit tests.
for t in test_balance test_flow test_pump test_powermeter test_powermeter_new \
         test_intake_anememoter test_outtaketake_anememoter; do
  [ -f "$SRC/$t.py" ] && cp "$SRC/$t.py" "$OUTPUT_DIR/"
done

echo "Package created in: $OUTPUT_DIR/"
echo ""
echo "NEXT STEPS:"
echo "1. Copy the folder to the Pi (scp -r $OUTPUT_DIR pi@<ip>:~/RPi_USB_Package) or via USB."
echo "2. Follow guides/SITE_VISIT_CHECKLIST.md (backup, sanity checks, outage test)."
echo "3. Run from ~/RPi_USB_Package:  python3 AquaPars1.py   (or AquaPars1_new_pm.py)"
echo ""
ls -lh "$OUTPUT_DIR/"
