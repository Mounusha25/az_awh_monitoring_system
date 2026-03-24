# test_anemometers.py
# ===============================
# Tester for both intake & outtake anemometers
# Reads both in parallel using threads
# ===============================

import time
import threading
from intake_anemometer import intake_anemometer
from outtake_anemometer import outtake_anemometer

# Shared variables
intake_data = None
outtake_data = None
running = True

def read_intake():
    global intake_data, running
    while running:
        try:
            intake_data = intake_anemometer()
        except Exception as e:
            print(f"[Intake] Error: {e}")
        time.sleep(1)

def read_outtake():
    global outtake_data, running
    while running:
        try:
            outtake_data = outtake_anemometer()
        except Exception as e:
            print(f"[Outtake] Error: {e}")
        time.sleep(1)

def main():
    global running

    print("Starting intake + outtake anemometer test. Press Ctrl+C to stop.")

    # Start threads
    t1 = threading.Thread(target=read_intake, daemon=True)
    t2 = threading.Thread(target=read_outtake, daemon=True)
    t1.start()
    t2.start()

    try:
        while True:
            if intake_data:
                h, t, v, unit = intake_data
                print(f"[Intake ] Humidity={h:.1f} %, Temp={t:.1f} °C, Velocity={v:.2f} {unit}")
            if outtake_data:
                h, t, v, unit = outtake_data
                print(f"[Outtake] Humidity={h:.1f} %, Temp={t:.1f} °C, Velocity={v:.2f} {unit}")
            print("-" * 60)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nExiting test...")
        running = False
        t1.join(timeout=1)
        t2.join(timeout=1)

if __name__ == "__main__":
    main()
