#!/usr/bin/env python3
"""
sim_run_on_mac.py
=================
Run the FULL AquaPars pipeline on your Mac (no RPi hardware needed).
  - Mocks: GPIO, serial balance, power meter, flow meter, anemometers
  - Real:  CSV saving, cloud upload, Tkinter UI

Usage:
    python3 sim_run_on_mac.py
"""

import sys
import os
import types
import random
import time
import threading

# ============================================================
# 1. Mock hardware libraries BEFORE any real imports
# ============================================================

# --- Mock gpiozero ---
class _MockOutputDevice:
    def __init__(self, pin, active_high=True, initial_value=False):
        self._pin = pin
        self._value = initial_value
        print(f"[SIM] MockOutputDevice on pin {pin}")
    def on(self):
        self._value = True
    def off(self):
        self._value = False
    def close(self):
        self._value = False

class _MockButton:
    def __init__(self, pin, pull_up=True):
        self._pin = pin
        self.when_pressed = None
        print(f"[SIM] MockButton on pin {pin}")

mock_gpiozero = types.ModuleType("gpiozero")
mock_gpiozero.OutputDevice = _MockOutputDevice
mock_gpiozero.Button = _MockButton
sys.modules["gpiozero"] = mock_gpiozero

# --- Mock lgpio (used by _free_gpio_pin) ---
mock_lgpio = types.ModuleType("lgpio")
mock_lgpio.gpiochip_open = lambda chip: 0
mock_lgpio.gpio_free = lambda h, pin: None
mock_lgpio.gpiochip_close = lambda h: None
sys.modules["lgpio"] = mock_lgpio

# --- Mock serial (pyserial) ---
class _MockSerial:
    def __init__(self, port=None, baudrate=9600, **kwargs):
        self.port = port
        self.is_open = True
        self.in_waiting = 0
    def read(self, size=1):
        return b""
    def readline(self):
        return b""
    def write(self, data):
        pass
    def close(self):
        self.is_open = False

mock_serial = types.ModuleType("serial")
mock_serial.Serial = _MockSerial
mock_serial.PARITY_NONE = "N"
mock_serial.STOPBITS_ONE = 1
mock_serial.EIGHTBITS = 8
mock_serial.SerialException = OSError
sys.modules["serial"] = mock_serial

# ============================================================
# 2. Now import the real modules (they'll pick up the mocks)
# ============================================================

# Override sensor functions with simulators AFTER module load
import intake_anemometer as _intake_mod
import outtake_anemometer as _outtake_mod
from pump_controller import PumpController
from read_balance import BalanceSerialReader, parse_balance_line
from awh_ui_layout import AWHControlPanel

# We can't import read_power / read_flow normally because their __init__
# checks for serial ports. We'll monkey-patch below after importing AquaPars1.

# ============================================================
# 3. Simulated sensor data generators
# ============================================================

_sim_weight = 0.0
_sim_weight_lock = threading.Lock()

def _sim_intake_anemometer(serial_port=None, baud_rate=None):
    """Return simulated intake anemometer data."""
    h = round(random.uniform(25, 45), 1)     # humidity %
    t = round(random.uniform(20, 35), 1)     # temperature °C
    v = round(random.uniform(0.5, 3.0), 2)   # velocity m/s
    print(f"[SIM Intake] Humidity={h}%, Temp={t}°C, Velocity={v} m/s")
    return h, t, v, "m/s"

def _sim_outtake_anemometer(serial_port=None, baud_rate=None, timeout=None):
    """Return simulated outtake anemometer data."""
    h = round(random.uniform(30, 55), 1)
    t = round(random.uniform(18, 30), 1)
    v = round(random.uniform(0.3, 2.5), 2)
    print(f"[SIM Outtake] Humidity={h}%, Temp={t}°C, Velocity={v} m/s")
    return h, t, v, "m/s"

# Patch the real modules so BalanceReader calls our simulators
_intake_mod.intake_anemometer = _sim_intake_anemometer
_outtake_mod.outtake_anemometer = _sim_outtake_anemometer


class SimBalanceReader:
    """Simulated balance that feeds fake weight lines to a callback."""
    def __init__(self, callback=None, interval=10, **kwargs):
        self.callback = callback
        self.interval = interval
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        global _sim_weight
        while self._running:
            with _sim_weight_lock:
                _sim_weight += round(random.uniform(0.1, 2.0), 4)
                w = _sim_weight
            line = f"ST,GS,+ {w:.4f}kg"
            if self.callback:
                self.callback(line)
            time.sleep(self.interval)


class SimPowerMeterReader:
    """Simulated power meter that feeds fake V/A/W/Wh to a callback."""
    def __init__(self, callback=None, interval=10, **kwargs):
        self.callback = callback
        self.interval = interval
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        total_wh = 0.0
        while self._running:
            V = round(random.uniform(118, 122), 1)
            A = round(random.uniform(0.5, 3.0), 3)
            W = round(V * A, 1)
            total_wh += W * (self.interval / 3600)
            if self.callback:
                self.callback(V, A, W, round(total_wh, 3))
            time.sleep(self.interval)


class SimFlowMeterReader:
    """Simulated flow meter that feeds fake flow data to a callback."""
    def __init__(self, pin=27, interval=1, callback=None, **kwargs):
        self.callback = callback
        self.interval = interval
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        total_liters = 0.0
        while self._running:
            flow_lmin = round(random.uniform(0.0, 2.5), 3)
            hz = round(flow_lmin * 46.7, 1)
            total_liters += flow_lmin * (self.interval / 60.0)
            if self.callback:
                self.callback(flow_lmin, hz, round(total_liters, 4))
            time.sleep(self.interval)


# ============================================================
# 4. Import AquaPars1 and monkey-patch its reader classes
# ============================================================

import AquaPars1

# Replace hardware reader classes with our simulators
AquaPars1.BalanceSerialReader = SimBalanceReader
AquaPars1.PowerMeterReader = SimPowerMeterReader
AquaPars1.FlowMeterReader = SimFlowMeterReader

# Replace anemometer functions used inside BalanceReader
AquaPars1.intake_anemometer = _sim_intake_anemometer
AquaPars1.outtake_anemometer = _sim_outtake_anemometer

# ============================================================
# 5. Run the app
# ============================================================

def main():
    csv_dir = "sim_measure_data"
    os.makedirs(csv_dir, exist_ok=True)

    pump = PumpController(pin=17, status_callback=None,
                          default_duration_min=2, initial_threshold_g=0)

    controller = AquaPars1.BalanceReader(
        callback=None,
        csv_dir=csv_dir,
        update_pump_status_callback=None,
        pump=pump,
    )

    app = AWHControlPanel(controller=controller)

    controller.callback = app.update_status
    controller.pump.set_status_callback(app.update_pump_status)

    print("\n" + "=" * 60)
    print("  AWH SIMULATION MODE — running on Mac")
    print("  CSV files will be saved to: sim_measure_data/")
    print("  All sensor data is SIMULATED (fake random values)")
    print("=" * 60 + "\n")

    app.mainloop()

    try:
        controller.csv_file.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
