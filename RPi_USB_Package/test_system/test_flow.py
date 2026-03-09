# test_flow.py
import time
from read_flow import FlowMeterReader

def flow_callback(flow, hz, total):
    print(f"[Flow Test] Flow = {flow:.2f} L/min | Freq = {hz:.1f} Hz | Total = {total:.3f} L")

if __name__ == "__main__":
    print("Starting Flow Meter Test... Press Ctrl+C to stop.")
    reader = FlowMeterReader(pin=27, interval=1, callback=flow_callback)
    reader.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping test...")
        reader.stop()