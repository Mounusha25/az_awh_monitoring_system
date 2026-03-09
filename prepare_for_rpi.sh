#!/bin/bash
# Prepare files for Raspberry Pi transfer
# This creates a clean package with only the files needed on RPi

OUTPUT_DIR="AWH_RPi_Package"

echo "Creating RPi package..."

# Clean previous package
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/test_system"

# Copy essential system files
cp AquaPars1.py "$OUTPUT_DIR/"
cp awh_ui_layout.py "$OUTPUT_DIR/"
cp pump_controller.py "$OUTPUT_DIR/"
cp read_balance.py "$OUTPUT_DIR/"
cp read_power.py "$OUTPUT_DIR/"
cp read_flow.py "$OUTPUT_DIR/"
cp intake_anemometer.py "$OUTPUT_DIR/"
cp outtake_anemometer.py "$OUTPUT_DIR/"

# Copy test files
cp test_system/test_*.py "$OUTPUT_DIR/test_system/"

# Create README for RPi
cat > "$OUTPUT_DIR/README_RPi.txt" << 'EOF'
AWH System for Raspberry Pi
============================

FILES INCLUDED:
---------------
AquaPars1.py              - Main program (run this!)
awh_ui_layout.py          - Control panel UI
pump_controller.py        - Pump control
read_balance.py           - Weight sensor reader
read_power.py             - Power meter reader
read_flow.py              - Flow meter reader
intake_anemometer.py      - Intake air sensor
outtake_anemometer.py     - Outtake air sensor
test_system/              - Individual sensor test files

SETUP ON RASPBERRY PI:
----------------------
1. Copy all files to: /home/pi/AWH_System/

2. Install dependencies:
   pip3 install RPi.GPIO pyserial requests

3. Connect all 5 USB sensors

4. Test each sensor:
   cd /home/pi/AWH_System/test_system/
   python3 test_balance.py
   python3 test_powermeter.py
   python3 test_flow.py
   python3 test_intake_anememoter.py
   python3 test_outtaketake_anememoter.py

5. Run the main system:
   cd /home/pi/AWH_System/
   python3 AquaPars1.py

WHAT IT DOES:
-------------
- Reads 5 sensors every 10 seconds
- Saves data to CSV files (measure_data/ folder)
- Uploads to cloud every 60 seconds
- Controls pump automatically based on weight
- Shows control panel UI

TROUBLESHOOTING:
----------------
- USB permission error: sudo usermod -a -G dialout pi (then reboot)
- GPIO error: Run with sudo python3 AquaPars1.py
- No sensors detected: Run lsusb to check connections
EOF

echo "✅ Package created in: $OUTPUT_DIR/"
echo ""
echo "NEXT STEPS:"
echo "1. Copy '$OUTPUT_DIR' folder to USB drive"
echo "2. Plug USB into Raspberry Pi"
echo "3. Copy files to /home/pi/AWH_System/"
echo "4. Open AquaPars1.py in Thonny and run it"
echo ""
ls -lh "$OUTPUT_DIR/"
