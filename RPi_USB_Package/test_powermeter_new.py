import time
from read_power_new import PowerMeterReader

def handle_power(v, a, w, kwh):
    print(f"Voltage={v} V | Current={a} A | Power={w} W | Energy={kwh} kWh")

def main():
    print("Starting power meter reader... Press Ctrl+C to stop.")
    reader = PowerMeterReader(callback=handle_power, interval=2)  # poll every 2 seconds
    reader.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        reader.stop()
        time.sleep(0.5)

if __name__ == "__main__":
    main()
