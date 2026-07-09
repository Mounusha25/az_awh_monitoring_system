# test_anemometer_intake.py
# ===============================
# Simple tester for intake anemometer
# Uses intake_anemometer() from intake_anemometer.py
# ===============================

import time
from intake_anemometer import intake_anemometer

def main():
    print("Starting intake anemometer test. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                humidity, temperature, velocity, unit = intake_anemometer()
                print(f"[Intake] Humidity={humidity:.1f} %, Temp={temperature:.1f} °C, Velocity={velocity:.2f} {unit}")
            except Exception as e:
                print(f"[Intake] Error: {e}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting intake anemometer test.")

if __name__ == "__main__":
    main()
