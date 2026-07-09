# test_pump.py
# ===============================

# Simple tester for PumpController
# ===============================

import time
from pump_controller import PumpController

def pump_status_cb(status):
    print(f"[Callback] Pump status changed → {status}")

def main():
    pump = PumpController(pin=17, status_callback=pump_status_cb,
                          default_duration_min=0.1,  # 6 seconds
                          initial_threshold_g=0)

    print("Pump test started. Commands:")
    print("  1 = manual ON")
    print("  2 = manual OFF")
    print("  3 = timed run (6 sec)")
    print("  q = quit")

    try:
        while True:
            cmd = input("Enter command: ").strip().lower()
            if cmd == "1":
                pump.manual_on()
            elif cmd == "2":
                pump.manual_off()
            elif cmd == "3":
                pump.start_timed(0.1)  # run for 6 seconds
            elif cmd == "q":
                break
            else:
                print("Invalid command")
    except KeyboardInterrupt:
        print("\nExiting pump test...")
    finally:
        pump.cleanup()

if __name__ == "__main__":
    main()
