# test_anemometer_outtake.py
# ===============================
# Simple tester for outtake anemometer
# Uses outtake_anemometer() from outtake_anemometer.py
# ===============================

import time
from outtake_anemometer import outtake_anemometer

def main():
    print("Starting outtake anemometer test. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                humidity, temperature, velocity, unit = outtake_anemometer()
                print(f"[Outtake] Humidity={humidity:.1f} %, Temp={temperature:.1f} °C, Velocity={velocity:.2f} {unit}")
            except Exception as e:
                print(f"[Outtake] Error: {e}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting outtake anemometer test.")

if __name__ == "__main__":
    main()
