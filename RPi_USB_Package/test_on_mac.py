#!/usr/bin/env python3
"""
test_on_mac.py
==============
Run this on your Mac to test the FULL AquaPars pipeline WITHOUT any RPi hardware.

It mocks:
  - PumpController  (no GPIO needed)
  - BalanceSerialReader  (generates fake weight data)
  - PowerMeterReader  (generates fake V/A/W/Wh data)
  - FlowMeterReader  (generates fake flow data)
  - intake_anemometer / outtake_anemometer  (fake temp/humidity/velocity)

Then runs the real:
  - BalanceReader controller (CSV saving, cloud upload logic)
  - AWHControlPanel UI (tkinter)

After ~30 seconds of running you can check the measure_data/ folder for CSV files.
"""

import sys
import os
import types
import random
import threading
import time

# ─── 1. Mock gpiozero BEFORE any import touches it ───────────────────────────

# Fake OutputDevice
class FakeOutputDevice:
    def __init__(self, pin=None, active_high=True, initial_value=False):
        self._on = initial_value
        print(f"[MOCK] OutputDevice pin={pin} (simulated, no real GPIO)")
    def on(self):
        self._on = True
    def off(self):
        self._on = False
    def close(self):
        pass

# Fake Button (for flow meter)
class FakeButton:
    def __init__(self, pin=None, pull_up=True):
        self.when_pressed = None
        print(f"[MOCK] Button pin={pin} (simulated)")

fake_gpiozero = types.ModuleType("gpiozero")
fake_gpiozero.OutputDevice = FakeOutputDevice
fake_gpiozero.Button = FakeButton
sys.modules["gpiozero"] = fake_gpiozero

# ─── 2. Mock serial (pyserial) ────────────────────────────────────────────────

fake_serial = types.ModuleType("serial")
fake_serial.Serial = lambda *a, **kw: None
fake_serial.SerialException = Exception
fake_serial.PARITY_NONE = 'N'
fake_serial.STOPBITS_ONE = 1
fake_serial.EIGHTBITS = 8
sys.modules["serial"] = fake_serial

# ─── 3. Mock lgpio ────────────────────────────────────────────────────────────

fake_lgpio = types.ModuleType("lgpio")
fake_lgpio.gpiochip_open = lambda chip: 0
fake_lgpio.gpio_free = lambda h, pin: None
fake_lgpio.gpiochip_close = lambda h: None
sys.modules["lgpio"] = fake_lgpio

# ─── 4. Now import the real modules (they'll use our mocks) ──────────────────

from pump_controller import PumpController
from read_balance import parse_balance_line
from awh_ui_layout import AWHControlPanel

# ─── 5. Fake sensor readers ──────────────────────────────────────────────────

class FakeBalanceSerialReader:
    """Generates fake balance lines like 'ST,GS,+ 123.4567kg' every interval seconds."""
    def __init__(self, callback=None, interval=10, **kw):
        self.callback = callback
        self.interval = interval
        self._running = False
        self._thread = None
        self._weight = 50.0  # start at 50g, slowly increase

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[MOCK] Balance reader started (fake data)")

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            # Simulate slow water collection
            self._weight += random.uniform(0.01, 0.5)
            line = f"ST,GS,+ {self._weight:.4f}kg"
            if self.callback:
                self.callback(line)
            time.sleep(max(1, self.interval))


class FakePowerMeterReader:
    """Generates fake power meter data."""
    def __init__(self, callback=None, interval=10, **kw):
        self.callback = callback
        self.interval = interval
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[MOCK] Power reader started (fake data)")

    def stop(self):
        self._running = False

    def _run(self):
        energy = 0.0
        while self._running:
            V = round(random.uniform(118.0, 122.0), 1)
            A = round(random.uniform(0.5, 2.5), 2)
            W = round(V * A, 1)
            energy += W * (self.interval / 3600.0)
            if self.callback:
                self.callback(V, A, W, round(energy, 2))
            time.sleep(max(1, self.interval))


class FakeFlowMeterReader:
    """Generates fake flow meter data."""
    def __init__(self, pin=27, interval=1, callback=None, **kw):
        self.callback = callback
        self.interval = interval
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[MOCK] Flow reader started (fake data)")

    def stop(self):
        self._running = False

    def _run(self):
        total = 0.0
        while self._running:
            flow_lmin = round(random.uniform(0.0, 2.0), 2)
            hz = round(flow_lmin * 46.7, 1)
            total += flow_lmin * (self.interval / 60.0)
            if self.callback:
                self.callback(flow_lmin, hz, round(total, 3))
            time.sleep(max(1, self.interval))


def fake_intake_anemometer():
    """Return (humidity, temperature, velocity, unit) — fake."""
    return (
        round(random.uniform(30, 60), 1),   # humidity %
        round(random.uniform(25, 35), 1),   # temperature °C
        round(random.uniform(0.5, 3.0), 2), # velocity
        "m/s"
    )


def fake_outtake_anemometer():
    """Return (humidity, temperature, velocity, unit) — fake."""
    return (
        round(random.uniform(70, 95), 1),   # humidity %
        round(random.uniform(20, 30), 1),   # temperature °C
        round(random.uniform(0.3, 2.0), 2), # velocity
        "m/s"
    )


# ─── 6. Monkey-patch imports used by AquaPars1 ───────────────────────────────

import intake_anemometer as ia_mod
import outtake_anemometer as oa_mod
import read_balance as rb_mod
import read_flow as rf_mod

# Replace hardware classes/functions with fakes
ia_mod.intake_anemometer = fake_intake_anemometer
oa_mod.outtake_anemometer = fake_outtake_anemometer
rb_mod.BalanceSerialReader = FakeBalanceSerialReader
# We need to also patch read_power, but it was imported via AquaPars1
# So let's create a fake module
fake_read_power = types.ModuleType("read_power")
fake_read_power.PowerMeterReader = FakePowerMeterReader
sys.modules["read_power"] = fake_read_power

# Patch read_flow FlowMeterReader
rf_mod.FlowMeterReader = FakeFlowMeterReader

# ─── 7. Now import and run AquaPars1 with all mocks in place ─────────────────

# We need to import AquaPars1's BalanceReader class
# But since it does `from xxx import xxx` at module level, we need the mocks
# to be in place BEFORE import. Let's re-import properly:
import importlib

# Force re-import of AquaPars1 so it picks up all our mocked modules
if "AquaPars1" in sys.modules:
    del sys.modules["AquaPars1"]

import AquaPars1

# ─── 8. Run! ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  AWH Test Mode — Running on Mac with SIMULATED sensors")
    print("  CSV files will be saved to: measure_data/")
    print("  Close the UI window to stop.")
    print("=" * 60)

    csv_dir = "measure_data"
    os.makedirs(csv_dir, exist_ok=True)

    # Create pump (uses mocked GPIO — no real hardware)
    pump = PumpController(pin=17, status_callback=None,
                          default_duration_min=2, initial_threshold_g=0)

    # Create controller
    controller = AquaPars1.BalanceReader(
        callback=None,
        csv_dir=csv_dir,
        update_pump_status_callback=None,
        pump=pump,
    )

    # Create UI
    app = AWHControlPanel(controller=controller)

    # Wire callbacks
    controller.callback = app.update_status
    controller.pump.set_status_callback(app.update_pump_status)

    print("\n[TEST] UI is starting. Click 'Validate' then 'Start' to begin.\n")

    app.mainloop()

    # Cleanup
    try:
        controller.csv_file.close()
    except:
        pass

    # Show what was saved
    print("\n" + "=" * 60)
    print("  Test complete! Checking saved CSV files:")
    print("=" * 60)
    if os.path.isdir(csv_dir):
        files = sorted(os.listdir(csv_dir))
        if files:
            for f in files:
                fpath = os.path.join(csv_dir, f)
                size = os.path.getsize(fpath)
                # Count data rows (excluding header)
                with open(fpath) as fh:
                    lines = fh.readlines()
                data_rows = len(lines) - 1 if len(lines) > 0 else 0
                print(f"  {f}  —  {data_rows} data rows, {size} bytes")
                if data_rows > 0:
                    print(f"    Header:    {lines[0].strip()[:80]}...")
                    print(f"    Last row:  {lines[-1].strip()[:80]}...")
        else:
            print("  (no CSV files found — did you click Start?)")
    print()


if __name__ == "__main__":
    main()
